# Fail Analysis Predictive Model

This is the official documentation for the settlement-fail prediction model. It is a living document: each build phase updates it, following the plans in [`plans/`](plans/00-master-plan.md). A plain-language overview with diagrams and a handover guide is in [`project-overview-and-handover.md`](project-overview-and-handover.md).

## Document control

| Field | Value |
|---|---|
| Model name | Fail Analysis Predictive Model (new-fail model v1; persistence model phase 5) |
| Version | 0.8.1 |
| Status | Phases 0–5 code done and validated on synthetic data; live runs wait on WSI access (Q1); the phase-5 severity target waits on `RiskCredit` and `NSCC` access (Q6) |
| Owner | Data & Decision Science — Pranay Koneni |
| Business owner | WTS Operations (Settlements / Fail Analysis) |
| Related products | Fail Analysis Tool (Ops Hub). PRD: `../fail-analysis-tracking-prd.docx.pdf`. EDD: `../FailAnalysisToolDevPlan.docx.pdf` |
| Source definitions | `../WTS Ops Mirror Repo/wsi-database-definitions/` (read-only mirror of WSI SQL Server and PostgreSQL objects) |
| Last updated | 2026-10-15 |

### Changelog

| Version | Date | Change | Plan |
|---|---|---|---|
| 0.1 | 2026-10-14 | Initial design: the initial proposal was reviewed against the WSI schema; target reframed to a trade-level new-fail model; sources, features, leakage rules and validation design documented | [00-master-plan.md](plans/00-master-plan.md) |
| 0.2 | 2026-10-14 | Phase 0a: WSI request ([requests/wsi-data-access-request.md](requests/wsi-data-access-request.md)) with the list of databases the wrapper cannot reach; 20 extract procedures and allowlist script in `sql/extract/`; wrapper client `failmodel.extract`; source registry with availability lags; added `p3_eod_prices`; corrected section 5.4 (P3 end-of-day snapshots exist in `ReplicateDB`) | [01-phase0-data-access.md](plans/01-phase0-data-access.md) |
| 0.3 | 2026-10-14 | Phase 0 profiling: read-only profiling SQL (`sql/profiling/01`–`05`); `failmodel.profiling` checks P1–P8 with config recommendations; synthetic test bed `failmodel.synthetic` (section 11.0); CLI `synth`, `extract`, `profile`; new provisional setting `p3_trade_record_types`; Q8 added | [02-phase0-source-profiling.md](plans/02-phase0-source-profiling.md) |
| 0.4 | 2026-10-14 | Phase 1: label build (CLI `labels`), reconciliation categories (DEC-11), `sql/labels/01`–`02`, label tests. Corrected the P3 report source: the Report reads RPE200/RPE201 from 2025-08-14 and the Summary reads them for all dates; `EODFAIMST_REPL` is only the currency list. Added the extract `usp_FailModel_Extract_P3RpeFails` (21 procedures) and RPE coverage in P8 (DEC-10) | [03-phase1-labels.md](plans/03-phase1-labels.md) |
| 0.5 | 2026-10-14 | Phase 2 features: 81 point-in-time features in 7 families, with section 6 now generated from code and a doc-sync test; as-of joins (`asof_join`, plus `pinned_join` for absence-means-zero sources, DEC-12) and a point-in-time `SecurityNumber`-to-CUSIP mapping; a leakage suite with negative controls (section 7); CLI `features` and `catalog`; synthetic results (section 11.3), the affirmation lag caveat and Q9 | [04-phase2-features.md](plans/04-phase2-features.md) |
| 0.6 | 2026-10-14 | Phase 3 models: rule and logistic benchmarks; deliver and receive LightGBM with rolling folds, a fold-only tuning grid, late out-of-fold calibration (method kept at deployment), out-of-time holdout and deployment refit; top-5% metrics; SHAP reason codes in plain language; versioned bundle with monitoring references; CLI `train`; synthetic results (section 11.4), where the deliver ECE fails because of base-rate drift and is reported as a fail; limitations added | [05-phase3-modeling.md](plans/05-phase3-modeling.md) |
| 0.7 | 2026-10-14 | Phase 4 shadow scoring: nightly population, a completeness gate with source roles and per-book masking of degraded sources, scoring and run logs (`failmodel.scoring`); PSI drift over pooled trailing days, realized performance, calibration and alerts, plus go-live checks (`failmodel.monitoring`); Redshift score table, latest view and monitoring table (`sql/scores/`); CLI `score`, `monitor`, `train --train-end`; out-of-sample synthetic shadow simulation with exact training/serving parity (section 11.5); section 12 rewritten; daily call count corrected to 18; DEC-13, DEC-14, Q10 | [06-phase4-shadow-scoring.md](plans/06-phase4-shadow-scoring.md) |
| 0.8 | 2026-10-14 | Phase 5 persistence and close-out risk (`failmodel.persistence`, CLI `persistence`): fail tracking across snapshots, for fails from trades that pass the v1 rules with entry by F (DEC-16); `still_open_h1/h3/h5`, Reg SHO 204 and municipal close-out labels; 21 fail-state and cure-history features on top of the 81 re-anchored v1 features; age-only benchmark; label reconciliation with snapshot counts; persistence leakage test with a negative control (section 7). The phase-3 engine now takes a `Task`, with new-fail results unchanged, and the purge widens to the label horizon. Fixed a look-ahead in the calendar family's load counts, which arose only with several scoring dates per settle date, so the new-fail table is unchanged. Severity profiling SQL (`sql/persistence/01`); synthetic results (section 11.6); sections 2, 3.2, 5.2, 13 and 14 updated; DEC-15 to DEC-19, Q11, Q12 | [07-phase5-fail-persistence.md](plans/07-phase5-fail-persistence.md) |
| 0.8.1 | 2026-10-15 | Added the companion [`project-overview-and-handover.md`](project-overview-and-handover.md), a plain-language overview with diagrams, synthetic-result charts and a handover runbook. Its charts are built from saved artifacts by `docs/make_figures.py` (`docs` extra: matplotlib). No model change | — |

---

## 1. Business objective

Operations reviews settlement fails after they happen, in the Fail Analysis tool (P3 and Beta today; Broadridge-first in the new Ops Hub build). By then the fail is already costing money:
- CNS fail charges;
- Reg SHO Rule 204 close-out exposure;
- the 10-day municipal close-out limit;
- buy-in risk;
- capital and reputational cost.

This model moves the review earlier. Each night it scores every trade due to settle the next business day with a calibrated probability that the trade will fail, plus the main reasons. That gives ops one night and the morning of settle date to act:
- borrow or recall shares;
- chase affirmation;
- contact the counterparty;
- pre-position inventory.

**Success** means that on a fixed daily review budget (the top K trades), ops catches a materially larger share of the trades and dollars that would have failed than today's rules (CUSIP already failing, or box deficit).

## 2. Scope and intended use

**In scope (v1)**
- Beta and P3 trades (customer and street side) with a contractual settle date from 2024-05-28 (the T+1 cutover) onward, in the same population the Fail Analysis report covers.
- Two models: fail-to-deliver (the priority) and fail-to-receive. Both books are pooled into each, with a `book` indicator.
- A daily batch score at about 22:30 ET on the business day before settlement.

**Phase 5 (follow-on; code done, section 3.2)**
- A fail-persistence and close-out risk model for fails that are already open. It predicts whether a fail is still open after 1, 3 and 5 business days, and whether it reaches the Reg SHO Rule 204 or the municipal close-out deadline unresolved.
- A fail-charge severity target (CUSIP × business day). It is blocked until `RiskCredit` or `NSCC` can be read (Q6).
- Its scores rank open fails for review. They do not replace the firm's Rule 204 and municipal close-out procedures.

**Out of scope**
- Same-day settling trades and trades entered on the settle date.
- Money-only items.
- Options (Beta `SecurityTypeCode` 'P'; P3 `SEC_TYPE` 5 and 6).
- Non-trade receive and deliver items (transfers, stock-loan returns).
- Broadridge-booked activity, until Broadridge fail labels exist (see section 8).

**Intended use.** Prioritizing ops work. The model is advisory. It must not trigger buy-ins, close-outs, regulatory reports or client communications automatically.

**Users**
- Settlement and fail analysts: the daily top-K list.
- Ops managers: trends and reasons.
- Stock loan: borrow needs.

## 3. Prediction unit, timing and label

### 3.1 v1 new-fail model

| Item | Definition |
|---|---|
| Unit | One trade (settlement obligation) per book. Beta key: `ControlNumber|TradeDate|AccountNumber|SecurityNumber`. P3 key: `TRADE_FULL_DATE|ACCT_NBR|CCURRENCY|ACCT_TYPE|MARKET|TRAN_NBR|FIRM_NBR` |
| S | Contractual settle date (`Trade.SettleDate`; `TRDDTL_ARCH.SETTLE_FULL_DATE`) |
| D (scoring date) | The business day before S. Features are taken as of D close |
| Scoring time | About 22:30 ET on D. That is after the DTCC 21:00 ET affirmation cutoff and the 22:00 ET P3 `EODFAIMST` snapshot (JAMS `Reports_and_Processes_Daily_Evening_M-F_10_PM`) |
| Direction (firm side) | Beta: `PurchaseOrSale = 'P'` → deliver, `'S'` → receive (matches `ReceiveDeliverCode` 0 and 1 in `GetBetaFailAnalysisData_Report`). P3: `BUY_SELL = 'B'` → deliver, `'S'` → receive (P3 fails: positive quantity is deliver, negative is receive). Confirmed in profiling check P6 |
| Label `failed_on_settle` | 1 if a fail record for the trade key is on the label snapshot for S (Age 1); 0 if that snapshot exists without a match; NULL (dropped) if the snapshot is missing |
| Extra label columns | `fail_quantity`, `fail_qty_fraction` (partial fails), `fail_notional`, `fail_settle_date_differs` |

```mermaid
sequenceDiagram
  participant T as Trade date T
  participant D as Scoring date D = S-1, 22:30 ET
  participant S as Settle date S
  participant L as Label snapshot for S
  T->>D: trade entered, confirms and affirmation by 21:00 ET
  D->>D: features as of D close; model scores; top-K list published
  D->>S: ops acts overnight and in the morning
  S->>L: P3 EODFAIMST 22:00 ET; Beta DailyArch snapshot
  L->>L: label 1 if the trade is still open at end of S
```

### 3.2 Phase-5 persistence model

The plan is [plans/07-phase5-fail-persistence.md](plans/07-phase5-fail-persistence.md); the code is `failmodel.persistence`.

| Item | Definition |
|---|---|
| Unit | One open fail per book, fail key and snapshot date F. The fail is in the report population and its business-day age is at least 1. It must come from an eligible trade (DEC-16): one that passes the v1 population rules, with entry by F in place of entry by S − 1. The match is by exact trade key, or else by a unique fallback-key match on the fail's first snapshot |
| Fail key | From phase 1. Beta: `FailUniqueId`, or the report's composite key when `ControlNumber` = 0. P3: the trade key plus `RECORD_TYPE` |
| Age | Business days from S to F, counting S as 1 (the report's Age 1) |
| Prediction time | When snapshot F is available: the P3 snapshot at 22:00 ET on F, the Beta snapshot on F + 1 |
| Features | The 81 v1 features, rebuilt with the scoring date set to F (each source at F − its lag), plus 21 fail-state and cure-history features (listed below). Fail state is allowed only here (DEC-15) |
| Models | Separate deliver and receive models per label, pooled across books with `book`; the phase-3 engine (folds, tuning grid, calibration, holdout, SHAP), with the purge widened to the label horizon (DEC-18) |
| Benchmark | `age_only`: the training rows' smoothed persistence rate by business-day age, capped at 30 |
| `still_open_h{1,3,5}` | 1 if the fail key is on the snapshot for F + h business days, 0 if that snapshot exists without it, NULL if it is missing |
| `reg204_breach` | Deliver-side equity fails only. 1 if the fail is on the snapshot for S + 3 business days. Labelled only for rows with F before that date (DEC-17) |
| `muni_breach` | Municipal fails, either direction. 1 if the fail is on the snapshot for S + 10 business days (OPS.WSP.F.14). Labelled only for rows with F before that date |
| Severity (blocked) | CNS fail charge per CUSIP × business day: `RiskCredit.public.nsccintradaycnsfailspositions.final_fails_charge`, with `NSCC.dbo.FailsDetailReport.CNSFailCharge` as the fallback. The profiling SQL is `sql/persistence/01_fail_charge_profile.sql` |

**Fail-state features** (family `fail_state`; all as of snapshot F):
- `age_bd`, `bd_since_first_seen`, `reappeared` (seen before but not on the previous business day's snapshot);
- `fail_qty_log`, `fail_amount_log`, `remaining_to_trade_qty` (below 1 after a partial cure), `remaining_to_original` (P3 `ORIGINAL_QUANTITY`), `qty_change_prev` (against the previous snapshot);
- `dk_count` and `fail_who` (Beta), and `fail_reason` and `fail_condition` (prefixed by book);
- cure history for h in {1, 3, 5}: `book_persist_rate_h`, `cusip_persist_rate_h` and `fail_cp_persist_rate_h`. These are the share of earlier fails still open h business days later, over the last 250 business days. Only fails whose horizon closed by F count (G + h ≤ F). CUSIP and counterparty rates shrink toward the book-and-direction rate with the same prior strength as the v1 history features.

## 4. Population rules

These mirror `OperationsPortalDB.dbo.GetBetaFailAnalysisData_Report` and `GetP3FailAnalysisData_Report`, so the labels reconcile with the report ops already uses.

| Rule | Beta | P3 |
|---|---|---|
| Account exclusions | `AccountType <> 8` | `ACCT_TYPE NOT IN ('2','3')` |
| Security exclusions | `SecurityType`/`SecurityTypeCode <> 'P'` | `SEC_TYPE NOT IN ('5','6')` |
| Cancels and corrections | `Cancelled = 0` | `CXL_COR_REASON` blank, `UNMATCHED_CANCEL <> 'Y'`, latest `SEQ_NBR` per key |
| Visible at scoring time | Entry date (`TradeTimestamp` date) ≤ D | `ENTRY_DATE` ≤ D |
| Non-zero quantity | `TradeQuantity <> 0` | `QUANTITY <> 0` |
| Settle date in window | S ≥ 2024-05-28 | S ≥ 2024-05-28 |

## 5. Data sources

All tables were verified against the mirrored DDL and the procedures that load them. "History" means the loader keeps one copy per date. "Current-state" means the table is truncated or overwritten, so it cannot be used for training without leaking.

### 5.1 Sources used

| ID | Database.table | As-of key | History | Role | Notes |
|---|---|---|---|---|---|
| `beta_trades` | `BetaAccessArchive.dbo.Trade` ∪ `BetaAccess.dbo.Trade` | `TradeDate` (S = `SettleDate`) | Yes; PK (`ID`, `TradeDate`), purged from live to archive | Trade universe | `Cancelled`, `ControlNumber`, `PurchaseOrSale`, `StandingInstructionCode`, `ContraDealerNumber`, `ContraBrokerNumber`, `ExecutionTime`, `TradeTimestamp` (entry), `FirmCapacityCode`, `foreign_curr` |
| `beta_fail_snapshots` | `BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo` | `SnapShotDate` | Yes | Label, fail pressure, lagged counterparty history | No PK. `SnapShotDate` = `ControlDB.DateMaster.ActualDate` (SortOrder 0) at load. Load timing is checked in P2 |
| `p3_trades` | `Phase3DB.dbo.TRDDTL_ARCH` | `TRADE_FULL_DATE`, `SETTLE_FULL_DATE` | Yes (coverage checked in P1) | Trade universe | Unique key includes `SEQ_NBR`, `SIDE_CODE`, `SIDE_SEQ_NBR`; deduplication rule set in P5 |
| `p3_fail_snapshots` | `Phase3DB.dbo.EODFAIMST` | `SnapShotDate` (calendar date of the 22:00 run) | Yes, since about 2020-03-30 | Label, fail pressure, lagged history | Daily copy of `ReplicateDB.FAIMST_REPL` |
| `beta_security` | `BetaDailyArchive.dbo.DailyArch_Security` | `SnapShotDate` | Yes; PK (`SnapShotDate`, `SecurityNumber`) | Security attributes | DTC chill and frozen flags, `DTCCNSEligibilityCode`, `NSCCEligible`, `BidPrice`, `AskPrice`, `LastClosePrice`, `RoundLotQuantity` |
| `beta_segregation` | `BetaDailyArchive.dbo.DailyArch_SegregationAnalysisInfo` | `SnapShotDate` | Yes | Box inventory | `Required`, `Available`, `StartOfDayDeficitQty`, `StockLoanQty`, `StockBorrowQty`, `DealerFailDeliverQty`, `DealerFailReceiveQty`, `InventoryLongQuantity`, `InventoryShortQuantity` |
| `beta_close_history` | `BetaAccessArchive.dbo.SecurityCloseHistory` | `TradeDate` | Yes; PK (`TradeDate`, `SecurityNumber`) | Liquidity | `ClosePrice`, `CloseVolume`. Replaces `DailyArch_SecurityPrice`, whose `LastClosePrice` is never populated |
| `beta_id_confirms` | `BetaDailyArchive.dbo.DailyArch_IDConfirms` | `SnapshotDate` | Each confirm is archived once | Institutional affirmation | `MatchedIndicator`, `ConfirmAffirmIndicator`, `AffirmPartyIndicator`. The first archived state is used; later status changes are lost |
| `beta_threshold_history` | `BetaAccess.dbo.SecurityExtensionHistory` | `EffectiveDate` (char(10)) | Change history | Reg SHO threshold flag | `ThresholdInd` |
| `cns_p3` | `Phase3DB.dbo.CNS_8199_S1_Data` | `SettleDate` + `Cycle` | Yes | Consecutive CNS fail days | `ClosingNetPosition` < 0 → fail to deliver. Replaces the truncated `CNS_FirmConsecutiveFailsTbl` |
| `cns_beta` | `NSCC.dbo.CNS_S1_Data` | `TradeDate` + `Cycle` | Yes | Consecutive CNS fail days | `TradeDate` probably holds the report or settle date (checked in P6) |
| `smartloan_avail` | `ReportDB.dbo.SmartLoanSLAvail` | `FileDate` | Yes | Lendable supply | `TotalAvailable`, `AvailableFromFirm/Customer/NonCustomer/BankLoan`. Never key on `TradeDate`, which is the next business day |
| `loanet_positions` | `ReportDB.dbo.LoanetOpenPositions` | `TradeDate` | Yes | Street tightness | `BorrowLoan`, `ContraParty`, `Quantity`, `Rate`, `Rebate` |
| `borrow_rates` | `ReportDB.dbo.StockBorrowInterestRate` | `FileDate` | Yes; PK (`FileDate`, `SecurityNumber`) | Borrow cost | `BorrowVWAR` |
| `reg204` | `ReportDB.dbo.CNS204FailSettlementData` | `ReportDate` | Yes | 204 threshold and start-of-day fails | `ThresholdIndex`, `SOD_CNS_FailDayQty`, `BorrowDesc`, all varchar |
| `business_calendar` | `ControlDB.dbo.DateMaster` | `ActualDate` | Yes | Business-day arithmetic | `IsMonthEnd`, `IsQuarterEnd`, `SettleDate`, `NextSettleDate` |
| `p3_eod_prices` | `ReplicateDB.dbo.EODCMVDAT_REPL` | `SnapShotDate` | Yes (dated snapshot) | P3 point-in-time price | `CURR_PRICE`, `POOL_FACTOR`. Replaces the current-state `SECBAS_REPL`/`POLDAT_REPL` price joins |
| `p3_rpe_fails` | `Phase3DB.dbo.RPE200` ∪ `RPE201` | `SnapShotDate` | Yes (dated file loads, with `UpdateDateTime`) | Report-side P3 fails after 2025-08-14; reconciliation only | `AccountName` and `RepCode` excluded. Not a feature source |

`p3_trades` is extracted as `ReplicateDB.dbo.TRDDTL_REPL` ∪ `Phase3DB.dbo.TRDDTL_ARCH`, deduplicated on the `SB_TRDDTL` key with the replicated row winning.

**P3 report source.** The model labels P3 from `Phase3DB.dbo.EODFAIMST` for every date. The Ops Portal procedures differ:
- `GetP3FailAnalysisData_Report` reads `EODFAIMST` (through `vw_EODFAIL_MASTER_PHASE3_DB_ONLY`) before 2025-08-14. From that date it reads the RPE200/RPE201 files through `vw_RPE_FILES_AND_EODFAIL_MASTER_COMBINED`.
- `GetP3FailAnalysisData_Summary` reads the RPE view for every date.
- `ReplicateDB.dbo.EODFAIMST_REPL` only supplies the non-USD currency list.

Profiling check P8 measures how well `EODFAIMST` covers the RPE rows after the switch (DEC-10).

Each source's extract procedure, as-of key and availability lag live in `failmodel.sources.SOURCES`. Lags are conservative (1 business day) until profiling confirms landing times, except the P3 fail snapshot (22:00 ET run, lag 0), trade records (visibility is enforced by the entry-date rule) and the calendar.

### 5.2 Conditional and phase-5 sources

| ID | Database.table | Condition |
|---|---|---|
| `stock_loan_borrow` | `NSCC.dbo.StockLoanBorrow` | `OpenReceiveDeliver` and `CNSNightlySettled` are computed from the open-fails table at run time. Used only if P3 shows a single run before 22:30 ET on D with no back-dated reruns |
| `borrow_for_needs` | `NSCC.dbo.BorrowForNeeds` | Same condition (`Deficit`, `OpenFailToDeliver`) |
| `locates` | `ReportDB.dbo.ShortSellStockLocate_Archive` | Keyed by symbol; needs a symbol → CUSIP map as of D |
| `fail_charges` | PostgreSQL `RiskCredit.nsccintradaycnsfailspositions` | Phase-5 severity target. Retention unconfirmed; not reachable through the SQL Server wrapper. Profiling SQL ready (`sql/persistence/01_fail_charge_profile.sql`, part A) |
| `fails_detail` | `NSCC.dbo.FailsDetailReport` | Phase-5 severity fallback (`CNSFailCharge`). Profiling SQL part B, including how many charged CUSIPs have an open fail in either book |
| `reg_sho_data` | `OperationsPortalDB.dbo.RegShoData` | Phase-5 close-out context |

### 5.3 Rejected or replaced sources

| Source | Reason |
|---|---|
| `BetaAccess.dbo.OpenFailReceiveDeliverInfo` | Truncated nightly; use the `DailyArch_` copy |
| `Phase3DB.dbo.CNS_FirmConsecutiveFailsTbl` (`dy`) | Truncated each run; rebuilt from CNS S1 history |
| `BetaAccess.dbo.SecurityPrice`, `DailyArch_SecurityPrice.LastClosePrice` | Truncated, or never populated; use `SecurityCloseHistory` |
| `BetaAccess.dbo.Account` (`CashStdInst1/2`), `ReplicateDB` `NMARED_REPL`, `NMADDA_REPL`, `SECBAS_REPL`, `POLDAT_REPL`, `STKREC_REPL`, `RROFFC_REPL` | Current-state. The DVP flag is taken from the trade record instead (Beta `StandingInstructionCode`, P3 `DEL_CODE`/`CUST_TYPE`) |
| `OperationsPortalDB.dbo.FailAnalysisComments` | Free text with no as-of cutoff in the report; excluded from v1 (a phase-5 candidate if filtered by `CreatedOn`) |
| `ReportDB.dbo.OCCRegSho` | Procedure comments say the data stops at 2017-03-31 |
| `ClosingNetPosition` as a table | It is a column in the CNS S1 tables |

### 5.4 Not available (requested in phase 0)

| Need | Status | Interim proxy |
|---|---|---|
| HTB/ETB lists | `LocateSecurity.dbo.EasyToBorrowListVersioned`, `Intranet.dbo.BorrowStatus`; not mirrored | `BorrowDesc` (204), Loanet rebate, SmartLoan availability, `ShortSellStockLocate_Archive.BorrowStatus` |
| Average daily volume | No table | Derived ADV20 from `SecurityCloseHistory.CloseVolume` |
| Shares outstanding | Current-state only (`SEDADS_REPL`) | Not used |
| P3 stock record and account history | Recent dated snapshots exist in `ReplicateDB` (`EODSTKREC_REPL`: `LOC_LOCATION`, `TD_QUANTITY`, `SD_QUANTITY`; `EODNMADDA_REPL`); older history is in `ReplicateDB_Archive`, which is not mirrored. Location-code semantics need WSI P3 input | P3 rows get missing box features until location codes are confirmed; LightGBM handles the missing values |
| P3 fail change log | `ReplicateDB_Archive.FAIMST_AUDIT_REPL` | Day-over-day snapshot comparison |

### 5.5 Access path and databases outside the wrapper

The WSI DB Wrapper connects to eight logical databases (`DatabaseConnectionResolver.cs`): `OperationsPortalDB`, `ReportDB`, `Phase3DB`, `P3ClearingDB`, `BetaAccess`, `ReplicateDB`, `ControlDB` and `WedbushClient`. Adding one is a wrapper code change and redeploy. The extract procedures are hosted in `OperationsPortalDB` and read other databases by three-part name, the way the allowlisted fail-analysis procedures already do.

| Database | Needed for | Wrapper status | Route requested |
|---|---|---|---|
| `BetaDailyArchive` | Beta label, security, box inventory, affirmations | Not a wrapper database | Cross-database read from `OperationsPortalDB`; already used by the allowlisted `GetBetaFailAnalysisData_Report` |
| `BetaAccessArchive` | Beta trade history, close price and volume | Not a wrapper database | Cross-database read (permission unconfirmed), or add to wrapper |
| `NSCC` | Beta CNS positions; conditional borrow tables; phase-5 fail charges | Not a wrapper database | Cross-database read (permission unconfirmed), or add to wrapper |
| `ReplicateDB_Archive` | Older P3 trade detail, P3 fail change log, archived stock record | Not mirrored, not a wrapper database | Grant plus wrapper route |
| `LocateSecurity` | HTB/ETB history | Not mirrored, not a wrapper database | Grant plus wrapper route |
| `Intranet` | Borrow status | Not mirrored, not a wrapper database | Grant plus wrapper route |
| PostgreSQL `RiskCredit` | Phase-5 severity target | Wrapper is SQL Server only | Separate endpoint, export or read-only login |
| `msdb` (optional) | Job timing for profiling | Not a wrapper database | One-time export from WSI |

## 6. Feature catalog

The catalog below is generated from the code (`failmodel.features.build.FEATURE_CATALOG`) with `failmodel catalog --doc failanalysispredictivemodel.md`. Do not edit it by hand: `tests/test_leakage.py` fails when it drifts from the code. Coverage per book and leakage status depend on the data, so they are reported with each feature build (`artifacts/reports/features_<date>.md`) and summarised in section 11.3.

How the as-of rules are applied (`failmodel.asof`):
- **Lag.** "≤ D − lag" uses the source's availability lag from `failmodel.sources` (section 5.1). The lag is 0 for P3 fail snapshots and for threshold history, and 1 for the other sources until profiling check P4 confirms landing times.
- **Latest row per key** (`asof_join`). Used for sources that carry every key every day: security reference, prices, SmartLoan, Loanet, borrow rates and Beta segregation. Values staler than `max_staleness_bd` (3) business days are set to missing.
- **Pinned snapshot** (`pinned_join`). Used for fail snapshots, CNS positions and Rule 204, where a key missing from a snapshot means zero. Each row is pinned to that source's latest snapshot date on or before the cutoff and joined exactly on that date. Values are 0 when the snapshot exists without the key, and missing when no fresh snapshot exists. Carrying a CUSIP's last row forward would revive fails that had already cured.
- **Security master mapping.** `SecurityNumber` is mapped to CUSIP through the security snapshot on or before each row's date, never the latest one.
- **Audit columns.** Every join writes the source date it used to an `_asof_<source>` column, which the as-of audit checks.

<!-- BEGIN FEATURE CATALOG (generated: failmodel catalog --doc) -->

| Feature | Family | Kind | Source | As-of rule | Books | Description |
|---|---|---|---|---|---|---|
| `book` | trade | categorical | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Book indicator (B = Beta, P = P3) |
| `log_notional` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | log(1 + \|net amount\|), falling back to quantity x price |
| `log_quantity` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | log(1 + \|quantity\|) |
| `log_price` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | log(1 + trade price) |
| `account_type` | trade | categorical | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Account type code |
| `counterparty_type` | trade | categorical | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Broker, Suspense (P3) or Customer, from the account number |
| `dvp_flag` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | DVP/RVP settlement (Beta StandingInstructionCode, P3 DEL_CODE; provisional codes) |
| `capacity` | trade | categorical | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Firm capacity code |
| `product_group` | trade | categorical | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | EQ, CORP, MUNI, FED or OTHER (explicit per-book mapping) |
| `exec_hour` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Execution time in hours |
| `late_day` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Executed at or after settings.late_day_hour |
| `is_asof` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Entered after the trade date |
| `entry_lag_bd` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Business days from trade date to entry |
| `entered_on_d` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Entered on the scoring date |
| `settle_cycle_bd` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Business days from trade date to S |
| `non_usd` | trade | numeric | `beta_trades, p3_trades` | Trade record; entry date <= D (population rule) | B, P | Non-USD settlement currency |
| `chill_any` | security | numeric | `beta_security` | SnapShotDate <= D - lag | B, P | Any DTC chill (deposit, COD, withdrawal, DO) |
| `dtc_frozen` | security | numeric | `beta_security` | SnapShotDate <= D - lag | B, P | DTC frozen flag |
| `cns_ineligible` | security | numeric | `beta_security` | SnapShotDate <= D - lag | B, P | DTCCNSEligibilityCode is not Y |
| `spread_bps` | security | numeric | `beta_security` | SnapShotDate <= D - lag | B, P | Bid-ask spread in basis points of mid |
| `log_close_price` | security | numeric | `beta_security` | SnapShotDate <= D - lag | B, P | log(1 + last close price) |
| `log_adv20` | security | numeric | `beta_close_history` | TradeDate <= D - lag | B, P | log(1 + 20-day average close volume) |
| `qty_to_adv20` | security | numeric | `beta_close_history` | TradeDate <= D - lag | B, P | Trade quantity / 20-day average volume |
| `vol20` | security | numeric | `beta_close_history` | TradeDate <= D - lag | B, P | 20-day standard deviation of daily log returns |
| `price_vs_eod` | security | numeric | `p3_eod_prices` | SnapShotDate <= D - lag | B, P | Trade price / latest end-of-day price - 1, clipped to [-1, 1] |
| `threshold_flag` | security | numeric | `beta_threshold_history` | max(EffectiveDate, AddedTimestamp) <= D | B, P | Reg SHO threshold security |
| `reg204_sod_fail_log` | security | numeric | `reg204` | ReportDate <= D - lag | B, P | log(1 + 204 start-of-day CNS fail quantity); 0 if not on the grid |
| `reg204_htb` | security | numeric | `reg204` | ReportDate <= D - lag | B, P | BorrowDesc marks the security hard to borrow |
| `reg204_threshold` | security | numeric | `reg204` | ReportDate <= D - lag | B, P | 204 grid threshold index |
| `box_deficit_log` | inventory | numeric | `beta_segregation` | SnapShotDate <= D - lag | B | log(1 + max(Required - Available, 0)) in the Beta box |
| `deficit_to_qty` | inventory | numeric | `beta_segregation` | SnapShotDate <= D - lag | B | Box deficit / trade quantity |
| `qty_to_box_available` | inventory | numeric | `beta_segregation` | SnapShotDate <= D - lag | B | Trade quantity / box Available |
| `sod_deficit_log` | inventory | numeric | `beta_segregation` | SnapShotDate <= D - lag | B | log(1 + start-of-day deficit quantity) |
| `stock_borrow_log` | inventory | numeric | `beta_segregation` | SnapShotDate <= D - lag | B | log(1 + stock borrow quantity) |
| `stock_loan_log` | inventory | numeric | `beta_segregation` | SnapShotDate <= D - lag | B | log(1 + stock loan quantity) |
| `no_loan_flag` | inventory | numeric | `beta_segregation` | SnapShotDate <= D - lag | B | NoLoanInd is Y |
| `sl_total_avail_log` | inventory | numeric | `smartloan_avail` | FileDate <= D - lag | B, P | log(1 + SmartLoan total available) |
| `qty_to_sl_avail` | inventory | numeric | `smartloan_avail` | FileDate <= D - lag | B, P | Trade quantity / SmartLoan total available |
| `sl_firm_share` | inventory | numeric | `smartloan_avail` | FileDate <= D - lag | B, P | Share of SmartLoan availability from firm inventory |
| `loanet_borrow_log` | inventory | numeric | `loanet_positions` | TradeDate <= D - lag | B, P | log(1 + street borrow quantity) |
| `loanet_loan_log` | inventory | numeric | `loanet_positions` | TradeDate <= D - lag | B, P | log(1 + street loan quantity) |
| `loanet_borrow_contras` | inventory | numeric | `loanet_positions` | TradeDate <= D - lag | B, P | Number of contras on the borrow side |
| `loanet_rebate` | inventory | numeric | `loanet_positions` | TradeDate <= D - lag | B, P | Average borrow rebate (low or negative = hard to borrow) |
| `loanet_min_rebate` | inventory | numeric | `loanet_positions` | TradeDate <= D - lag | B, P | Minimum borrow rebate |
| `borrow_vwar` | inventory | numeric | `borrow_rates` | FileDate <= D - lag | B, P | Volume-weighted average borrow rate |
| `borrow_count` | inventory | numeric | `borrow_rates` | FileDate <= D - lag | B, P | Number of borrows |
| `confirm_seen` | affirmation | numeric | `beta_id_confirms` | SnapshotDate <= D - lag (each confirm archived once) | B (DVP) | An institutional confirm for the trade is visible |
| `confirm_matched` | affirmation | numeric | `beta_id_confirms` | SnapshotDate <= D - lag (each confirm archived once) | B (DVP) | Visible confirm is matched |
| `confirm_affirmed` | affirmation | numeric | `beta_id_confirms` | SnapshotDate <= D - lag (each confirm archived once) | B (DVP) | Visible confirm is affirmed |
| `cusip_open_fails_same_dir` | fail_pressure | numeric | `beta_fail_snapshots / p3_fail_snapshots` | Latest snapshot <= D - lag (B lag 1, P lag 0) | B, P | Open fails in the CUSIP, same book and direction |
| `cusip_open_fail_qty_same_dir_log` | fail_pressure | numeric | `beta_fail_snapshots / p3_fail_snapshots` | Latest snapshot <= D - lag (B lag 1, P lag 0) | B, P | log(1 + open fail quantity in the CUSIP, same direction) |
| `cusip_open_fails_opp_dir` | fail_pressure | numeric | `beta_fail_snapshots / p3_fail_snapshots` | Latest snapshot <= D - lag (B lag 1, P lag 0) | B, P | Open fails in the CUSIP, opposite direction |
| `cusip_aged_fail_qty_log` | fail_pressure | numeric | `beta_fail_snapshots / p3_fail_snapshots` | Latest snapshot <= D - lag (B lag 1, P lag 0) | B, P | log(1 + open fail quantity aged >= aged_fail_min_age_bd), same direction |
| `qty_to_cusip_open_fail_qty` | fail_pressure | numeric | `beta_fail_snapshots / p3_fail_snapshots` | Latest snapshot <= D - lag (B lag 1, P lag 0) | B, P | Trade quantity / open fail quantity in the CUSIP, same direction |
| `account_open_fails` | fail_pressure | numeric | `beta_fail_snapshots / p3_fail_snapshots` | Latest snapshot <= D - lag (B lag 1, P lag 0) | B, P | Open fails on the account, both directions |
| `book_open_fails_same_dir_log` | fail_pressure | numeric | `beta_fail_snapshots / p3_fail_snapshots` | Latest snapshot <= D - lag (B lag 1, P lag 0) | B, P | log(1 + open fails in the book, same direction) |
| `cns_ftd_qty_log` | fail_pressure | numeric | `cns_beta / cns_p3` | Latest position date <= D - lag; end-of-day cycle | B, P | log(1 + CNS fail-to-deliver quantity in the CUSIP) |
| `cns_consec_ftd_days` | fail_pressure | numeric | `cns_beta / cns_p3` | Latest position date <= D - lag; end-of-day cycle | B, P | Consecutive CNS position dates with a fail to deliver |
| `cp_fail_rate_20` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by counterparty, last 20 business days |
| `cp_fail_rate_60` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by counterparty, last 60 business days |
| `cp_fail_rate_250` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by counterparty, last 250 business days |
| `acct_fail_rate_20` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by account, last 20 business days |
| `acct_fail_rate_60` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by account, last 60 business days |
| `acct_fail_rate_250` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by account, last 250 business days |
| `cusip_fail_rate_20` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by CUSIP (both books), last 20 business days |
| `cusip_fail_rate_60` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by CUSIP (both books), last 60 business days |
| `cusip_fail_rate_250` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Smoothed fail rate by CUSIP (both books), last 250 business days |
| `cp_days_since_fail` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Business days since the last fail by counterparty |
| `acct_days_since_fail` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Business days since the last fail by account |
| `cusip_days_since_fail` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Business days since the last fail by CUSIP (both books) |
| `cp_trades_60_log` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | log(1 + labelled trades with the counterparty, last 60 business days) |
| `global_fail_rate_250` | history | numeric | `phase-1 labels` | Settle date S' <= D - label_availability_lag_bd | B, P | Fail rate of the book and direction, last 250 business days |
| `settle_dow` | calendar | numeric | `business_calendar` | Known in advance | B, P | Day of week of S (0 = Monday) |
| `settle_month_end` | calendar | numeric | `business_calendar` | Known in advance | B, P | S is the last business day of the month |
| `settle_quarter_end` | calendar | numeric | `business_calendar` | Known in advance | B, P | S is the last business day of the quarter |
| `settle_post_holiday` | calendar | numeric | `business_calendar` | Known in advance | B, P | S is the first business day after a market holiday |
| `book_trades_on_s_log` | calendar | numeric | `beta_trades, p3_trades` | Trades entered <= D | B, P | log(1 + trades in the book settling on S) |
| `book_trades_entered_on_d_log` | calendar | numeric | `beta_trades, p3_trades` | Trades entered <= D | B, P | log(1 + trades entered in the book on D) |
| `cusip_trades_on_s` | calendar | numeric | `beta_trades, p3_trades` | Trades entered <= D | B, P | Trades in the CUSIP and book settling on S |
| `cusip_net_deliver_qty_log` | calendar | numeric | `beta_trades, p3_trades` | Trades entered <= D | B, P | Signed log(1 + \|deliver - receive quantity\|) in the CUSIP on S |
| `trade_share_of_cusip_qty` | calendar | numeric | `beta_trades, p3_trades` | Trades entered <= D | B, P | Trade quantity / same-direction quantity in the CUSIP on S |

**Deferred features**

| Feature | Family | Why deferred |
|---|---|---|
| `dk_count_by_counterparty` | history | Beta fail counterparties are NSCC numbers while trade counterparties are contra broker numbers; a lagged DK count needs a mapping between the two ID spaces (open question). |
| `counterparty_open_fails` | fail_pressure | Same ID-space mismatch as dk_count_by_counterparty. |
| `smartloan_buckets` | inventory | SmartLoan bucket columns wait on the P4 column profile; only totals are used. |
| `locate_demand` | inventory | Locates live in LocateSecurity, which the wrapper cannot reach (access request). |
| `borrow_needs` | inventory | StockLoanBorrow / BorrowForNeeds history depth is unconfirmed (P3). |

<!-- END FEATURE CATALOG -->

## 7. Leakage rules

1. **Outcome attributes are never features of the trade being scored.** This covers `DKCount`, `DTCReasonCode`, `FailWho`, `FailConditionCode`, `FAIL_REASON` and `StockLoanRate`. They describe a fail after it happens, and are allowed only as lagged aggregates over prior settle dates.
2. **Archives only.** Every feature comes from a dated archive, filtered by its as-of key. Joins to current-state tables are forbidden.
3. **Availability lag.** Each source has an availability lag. Sources that land after 22:30 ET on D get lag 1 (set in phase 0).
4. **Label-derived history uses closed horizons only.** History features may use trades with S ≤ D − `label_availability_lag_bd`.
5. **Missing label snapshot.** A missing snapshot gives an unknown label, never a negative.
6. **Comments.** Comments and other free text are excluded from v1.
7. **Cancellation.** Cancellation status in the archive is final state. Cancelled trades are dropped from training, which is a population choice, not a feature. At scoring time, live trades are scored whether or not they are later cancelled. This mismatch is monitored. For the same reason, the calendar family's load counts include trades that were later cancelled.
8. **Own outcome excluded from pressure features.** Fail-pressure features read snapshots on or before D − lag, and history features read labels with S′ ≤ D − 1. Neither can contain the scored trade's own fail, which first appears on the snapshot for S.
9. **Fail state only in the persistence model** (DEC-15). In phase 5 the fail already exists on snapshot F, so its state there (age, quantity, reason, condition, DK count) is known at prediction time. Later snapshots of the same fail are never read. Cure-history rates count only fails whose horizon closed by F. Rule 1 still applies to the v1 model.
10. **Automated tests** (`failmodel.leakage`, `tests/test_leakage.py`, `tests/test_asof.py`). They run in the test suite, and every feature build (`failmodel features`) reports them:

| Check | What it does | Passes when |
|---|---|---|
| Forbidden columns | Compares feature names with the outcome fields above, the label columns, `fail_*` names, and `cancelled`, `exclusion_reason`, `match_method` | Nothing matches |
| As-of audit | For every `_asof_<source>` column, compares the source date used with D − lag of that source | Zero violations |
| Future perturbation | Rebuilds the whole pipeline (labels, history, every family) from raw extracts in which every row known after a cutoff is randomized (`noise`) or removed (`drop`). Compares features for rows with D ≤ cutoff | Zero differing values |
| Single-feature screen | ROC-AUC of each feature alone (categoricals by their in-sample fail rate) | No feature above 0.95, otherwise it is flagged for review |
| Label shuffle | LightGBM trained on permuted labels, time-ordered 70/30 split | Holdout PR-AUC ≤ max(1.5 × base rate, base rate + 0.01) |
| Persistence future perturbation (`tests/test_persistence.py`) | Rebuilds the persistence table (fail tracking, cure history and the re-anchored v1 features) from extracts with every row after a cutoff removed. Compares all 102 features for rows with F ≤ cutoff | Zero differing values |

The tests include negative controls, so a pass means something:
- A security lag set to −1 (reading D + 1) must be caught by the perturbation test.
- A source date shifted 2 business days forward must be caught by the as-of audit.
- A noisy copy of the label must be flagged by the screen.
- In the persistence test, a fail's lifetime snapshot count, which does see the future, must differ after truncation.

The persistence perturbation test found a real look-ahead in the calendar family's load counts. They were grouped by settle date only, so a fail re-anchored at F counted trades entered after F. In v1 every settle date has one scoring date, so the new-fail table was not affected. The fix groups by scoring date too (v0.8).

## 8. Beta/P3 harmonization and the Broadridge transition

**Canonical schema.** Both books map into one canonical trade and fail schema (phase 1, `failmodel.canonical`), so the pooled model sees the same feature definitions.

Known differences:
- **Security-type codes invert between books** (business rule BR-7 in the fail-analysis analysis). Product groups are mapped explicitly:
  - Beta: Equities = C, Corporates = B, Municipals = M, Fed-eligible = T.
  - P3: Equities = A/B, Corporates = J/S, Municipals = L (or O with class FMB/MUN), Fed-eligible = M/P/Q/R/O.
- **Counterparty type:**
  - Beta: account 100000–999999 → Broker.
  - P3: `ACCT_NBR` `94%` → Broker; `89%` and `91%` → Suspense.
  - DVP comes from the trade record in both books.
- **Feature coverage by book** (feature build, phase 2):
  - **Beta-only:** box inventory (`beta_segregation`: 7 features) and affirmation (`beta_id_confirms`: 3 features, Beta DVP trades only). They stay Beta-only until `ReplicateDB_Archive` access is granted. P3 rows carry missing values, and `book` lets the model condition on that.
  - **Both books by CUSIP:** security, liquidity and Reg SHO features. These come from Beta reference tables (`DailyArch_Security`, `SecurityCloseHistory`, `SecurityExtensionHistory`) and from street-wide files (SmartLoan, Loanet, borrow rates, Rule 204). They are keyed by CUSIP-9, so P3 trades are covered for any CUSIP that Beta's security master carries.
  - **Book-specific source, shared definition:** fail pressure. It reads the trade's own book's fail snapshots, with the Beta snapshot lagged 1 business day and P3's `EODFAIMST` lagged 0, and the matching CNS file (`cns_beta` or `cns_p3`).
  - **Pooled:** CUSIP fail-rate history combines both books. Counterparty and account histories stay within a book because their ID spaces differ.

**Broadridge.** The Broadridge mart (`ops.fail_and_stock_loan_exceptions`) has no real fail flag yet: `is_exception_candidate` is false and `fail_reason` is NULL. So the Broadridge book cannot be trained on yet.

When the settlement-obligations feed (Ops Hub decision D-02) delivers a fail status:
1. Map Broadridge fields to the canonical schema as `book = 'BR'`.
2. Run the v1 model in shadow on Broadridge trades.
3. Retrain with Broadridge labels after at least 3 months of history.

Until then, v1 learns only legacy-book behavior. That is a known transfer risk (section 13).

## 9. Modeling approach

- **Benchmarks**
  - A rule baseline: the CUSIP already has an open fail in the same direction, or the Beta box is in deficit; ties ranked by notional.
  - Logistic regression.
- **Primary model:** LightGBM, binary, with:
  - native categorical features;
  - `scale_pos_weight` for imbalance;
  - early stopping on the validation fold.
- **Model structure:** separate deliver and receive models; each is pooled across books with a `book` feature.
- **Calibration:** isotonic, falling back to sigmoid when there are fewer than 500 positives.
- **Explanations:** LightGBM native SHAP contributions, giving the top three plain-language reasons per trade.

**As implemented** (`failmodel.models`, CLI `train`):
- **Rule baseline** (`baseline.rule_score`): 1 when `cusip_open_fails_same_dir` > 0 or `box_deficit_log` > 0. Ties are broken by notional, scaled into [0, 0.5) so the rule always dominates.
- **Logistic benchmark:** median imputation and standardization; one-hot categoricals (levels with fewer than 20 rows pooled); `class_weight="balanced"`, C = 0.5. Its probabilities are not calibrated, so it is compared on ranking metrics only.
- **LightGBM:**
  - Categorical levels are frozen from the development window; unseen levels become missing.
  - `scale_pos_weight` = negatives / positives in each training set.
  - Early stopping on fold PR-AUC (`average_precision`).
  - `--tune` grid-searches `num_leaves` ∈ {15, 31, 63} × `min_data_in_leaf` ∈ {50, 100, 200} on the rolling folds only. The holdout is never used to choose anything.
  - The holdout model and the deployed model use the median best iteration across folds.
- **Calibration:** fitted on the pooled out-of-fold predictions of the last 2 folds. The method chosen there is kept for the deployed calibrator, so what ships is what was validated.
- **Deployment refit:** the holdout figures come from a model trained only on data before the holdout purge. The deployed model is then refit on every labelled row with the same rounds. Its calibrator is refit on the late out-of-fold plus holdout predictions, all of which are out of time.
- **Reason codes** (`explain.reason_codes`):
  - Up to 3 features with a positive SHAP contribution, in log-odds.
  - Text comes from the catalog description and the feature value. Log features are shown on their original scale, for example "Open fail quantity in the CUSIP, same direction: 1,500".
- **Bundle** (`artifacts/models/<version>/`): models, calibrators, frozen category levels, `manifest.json`, `metrics.json`, `model_card.md`, and `reference_<D|R>.json`. The reference holds training feature distributions, the holdout score distribution and holdout metrics for phase-4 monitoring. `load_bundle` refuses a bundle whose feature list differs from the code.

## 10. Validation and metrics

- **Splits**
  - Rolling origin by S: six monthly folds, with a 5-business-day purge between train and validation.
  - A final 3-month out-of-time holdout, untouched by tuning.
- **Metrics**
  - PR-AUC (primary) and ROC-AUC.
  - Brier score and expected calibration error.
  - Daily precision and recall at K in {25, 50, 100}.
  - Dollar-weighted recall at K: the failed notional of caught fails over all failed notional, falling back to trade notional.
  - The same three metrics for the daily top 5% of trades. This version does not depend on daily volume, and it is the useful one until ops sets K (Q5).
  - ECE uses 10 equal-frequency bins, because equal-width bins are nearly empty at these base rates.
- **Slices:** book, direction, product group, counterparty type and notional band.
- **Acceptance**
  - LightGBM beats the rule baseline on holdout PR-AUC and on precision at K = 50 (deliver model).
  - ECE ≤ 0.02, or within 25% of the base rate.
  - No fold's PR-AUC is below 70% of the fold average.
  - Also reported, but not gating: whether LightGBM beats the logistic benchmark, and whether it beats the rule at the top 5%.
- **Regime checks**
  - Labels start at the T+1 cutover (2024-05-28).
  - Labels use one definition across the 2025-08-14 P3 report source switch, because they are built directly from `EODFAIMST`, not from the RPE view. Whether they still agree with the report after the switch is measured in P8.

## 11. Results

Results are added per phase. Real-data results require the phase-0 access to be in place.

### 11.0 Synthetic test bed

Until WSI deploys the extract procedures, every phase is validated end to end on synthetic data (`failmodel.synthetic`, CLI `synth`).

- **Layout.** The generator writes all 17 non-conditional sources with the same column names, codes and types as the extract procedures. The pipeline cannot tell synthetic extracts from real ones.
- **Planted structure.** The test bed contains known structure, so each check has a right answer:
  - a latent borrow hardness per CUSIP drives spreads, chills, box deficits, SmartLoan, Loanet rebates, CNS positions, the threshold list and Rule 204;
  - fail probability depends on that hardness and on counterparty and account quality, DVP and affirmation, execution time, size, existing fail pressure and municipal product;
  - fails cure or partially cure day by day;
  - non-trade fails, Beta fails with `ControlNumber = 0`, and missing snapshot days are included;
  - landing timestamps are set so the right lags are known (Beta fail snapshot 25.5 h after midnight of `SnapShotDate`, `DailyArch_*` 26 h, SmartLoan 21 h, CNS 7 h).
- **What it shows.** Synthetic results show that the code recovers planted structure. **They say nothing about real model performance.** Every synthetic figure in this section is labelled as synthetic.

Default synthetic set: 2024-01-02 to 2025-06-30, 400 CUSIPs and 150 trades a day per book (56k Beta and 59k P3 trades). Base rates are Beta deliver 8.2%, Beta receive 2.9%, P3 deliver 8.3% and P3 receive 2.9%.

### 11.1 Phase 0: source profiling

**Status.** The code is done: `sql/profiling/01`–`05` (read-only SQL for the WSI DBA or a wrapper session) and `failmodel.profiling` (checks P1–P8 over the landed extracts, with config recommendations). Live results are pending access (Q1). Run `failmodel profile` once the extracts land; the report goes to `artifacts/reports/profile_<date>.md`.

**Definition-level findings.** These come from the mirrored DDL and procedure code, not from data:

| Check | Finding | Effect on config |
|---|---|---|
| P1 history | `TRDDTL_ARCH`, `EODFAIMST`, `fal` and the `DailyArch_*` tables are dated archives; `DateMaster` covers the calendar. Depth is unknown until P1 runs | `extract_start` stays 2023-05-01 |
| P2 Beta snapshot | `fal.UpdateDateTime` is the only load timestamp; `SnapShotDate` semantics are unconfirmed (Q2) | `beta_snapshot_offset_bd` 0 and `label_availability_lag_bd` 1, both provisional |
| P3 borrow runs | `StockLoanBorrow` and `BorrowForNeeds` have no load timestamp | Both stay conditional and are unused in v1 |
| P4 landing | Timestamps exist for `fal`, the `DailyArch_*` tables, SmartLoan and the CNS tables; Loanet, borrow rates and Rule 204 have none | Untimed sources keep lag 1 (DEC-8) |
| P5 join | Beta fails carry `ControlNumber`; P3 fails carry the full `SB_TRDDTL` key. Non-trade P3 rows are identified only by `RECORD_TYPE` (Q8) | `p3_trade_record_types` provisional (None = all rows trade-originated) |
| P6 semantics | P3 fail-row direction comes from quantity and amount sign; `BUY_SELL` mapping B → D, S → R is provisional | Direction maps provisional |
| P7 base rates | Needs data | — |
| P8 P3 source switch | From 2025-08-14 the Report reads the RPE200/RPE201 files (the Summary reads them for all dates); labels read `EODFAIMST` | P8 compares Age-1 keys in the two on the view's join key; below 95% coverage triggers DEC-10 review |

**Synthetic validation (synthetic data, not real results).** Every planted value was recovered (`tests/test_profiling.py`):

| Setting | Planted | Recommended by profiling |
|---|---|---|
| `beta_snapshot_offset_bd` | 0 | 0 (modal first-appearance lag) |
| `label_availability_lag_bd` | 1 (lands 25.5 h after midnight) | 1 |
| `lag.beta_security`, `lag.beta_segregation` | 1 (26 h) | 1 |
| `lag.smartloan_avail` | 0 (21 h) | 0 |
| `lag.cns_beta`, `lag.cns_p3` | 0 (7 h) | 0 |
| `cns_eod_cycle` | C02 (latest) | C02 |
| `p3_trade_record_types` | F (trade); T (non-trade) | F (100% matched vs 0%) |
| Missing snapshot days | 2 Beta, 1 P3 | Detected by P1 |

Two defects in the test bed were found and fixed while running this check:
- **Non-trade fails split into several fails.** They drew a new account on every snapshot row, so one fail looked like several, each first seen after its settle date.
- **Base rates were too high**, at 15% for deliveries. The intercepts were lowered.

**Recommended configuration.** These are the current provisional values, to be confirmed by the live P1–P8 run (`config.PROVISIONAL`):

| Setting | Value | Confirmed by |
|---|---|---|
| `beta_snapshot_offset_bd` | 0 | P2 |
| `label_availability_lag_bd` | 1 | P2 |
| `cns_eod_cycle` | None (latest cycle per date) | P6 |
| `p3_dedup_rule` | `latest_seq_nbr` | P5 |
| `p3_trade_record_types` | None | P5 |
| `beta_direction_map`, `p3_direction_map` | P → D, S → R; B → D, S → R | P6 |
| `beta_dvp_codes`, `p3_dvp_codes` | `5`; `V` | P6 |
| Source lags | Section 5.1 | P4 |

### 11.2 Phase 1: trade universe and labels

**Status.** The code is done:
- `failmodel.canonical` and `failmodel.labels`, with CLI `labels`. The command writes `processed/{trades,fails,labels}.parquet` and `artifacts/reports/labels_<date>.md`.
- Two CSVs for server-side cross-checks: `label_daily_counts.csv` against `sql/labels/01_label_reference.sql`, and `age1_reconciliation_daily.csv` against `sql/labels/02_reconcile_age1.sql`.
- `tests/test_labels.py` covers exact and fallback matching, partial fails, the NULL label for a missing snapshot, every exclusion, the latest-`SEQ_NBR` deduplication, direction mapping and every reconciliation category.

The live label build and the five-day spot check against `GetBeta/P3FailAnalysisData_Summary @AgeFilter = '1'` wait on Q1.

**Reconciliation categories.** Every Age-1 fail, counted with the report's filters, falls into exactly one category:

| Category | Meaning | Counts toward the 90% target |
|---|---|---|
| `matched_in_population` | Matched to an in-population trade, so it is a positive label | Yes (matched) |
| `matched_excluded_trade` | Matched to a trade outside the model population, for example entered on S | Yes (matched) |
| `unmatched_trade_missing` | Comes from a trade (Beta `ControlNumber` ≠ 0, or a P3 trade record type), but no trade was found | Yes (unmatched) |
| `unmatched_zero_control` | Beta, `ControlNumber = 0`, with no fallback match: non-trade activity | No |
| `unmatched_non_trade_record` | P3 record type outside `p3_trade_record_types` | No |

**Synthetic results (synthetic data, not real results).** From the default synthetic set; labels start 2024-05-28 and cover 271 settle dates:

| Book | Age-1 fails | Matched, in population | Matched, excluded trade | Unmatched, trade missing | Unmatched, non-trade | Match rate of trade-originated |
|---|---|---|---|---|---|---|
| Beta | 2,555 | 1,939 | 234 (entered on S) | 0 | 382 (zero control) | 100% |
| P3 | 1,287 | 988 | 105 (entered on S) | 194 | 0 | 84.9% with `p3_trade_record_types = None`; 100% with `('F',)` |

All 194 unmatched P3 fails are planted non-trade records (`RECORD_TYPE` T). This is why `p3_trade_record_types` must be set from the live P5 result (Q8) before the 90% exit criterion can be judged.

| Book / direction | Trades | Fails | Base rate | Notional fail rate | Unknown labels |
|---|---|---|---|---|---|
| Beta deliver | 17,428 | 1,436 | 8.24% | 13.0% | 123 |
| Beta receive | 17,670 | 503 | 2.85% | 3.8% | 132 |
| P3 deliver | 8,808 | 731 | 8.30% | 13.1% | 31 |
| P3 receive | 8,868 | 257 | 2.90% | 4.3% | 36 |

Unknown labels equal the trades settling on the 2 planted missing Beta snapshot days and the 1 missing P3 day. They are dropped, not counted as negatives.

### 11.3 Phase 2: point-in-time features

**Status.** The code is done:
- `failmodel.asof`: `asof_join`, `pinned_join`, `reference_dates` and `knowledge_date`.
- `failmodel.features`: one module per family (`trade`, `security`, `inventory`, `affirmation`, `fail_pressure`, `history`, `calendar_features`) and `build`, which holds the catalog, context, coverage and markdown.
- `failmodel.leakage`.
- CLI `features`, which writes `processed/features.parquet`, `reports/features_<date>.md`, `feature_coverage.csv`, `single_feature_screen.csv` and `feature_catalog.md`. Pass `--perturb-cutoff` to add the perturbation test.
- CLI `catalog`.
- Tests: `tests/test_asof.py` (4) and `tests/test_leakage.py` (11, including the negative controls).

The live build and the perturbation test on the full extract wait on Q1.

**Synthetic results (synthetic data, not real results).** From the default synthetic set:
- 53,096 trade rows, of which 52,774 have a known label.
- Settle dates 2024-05-28 to 2025-06-30.
- 81 features (5 categorical).
- The labels feeding the history family span the whole extract window (2024-01-02 onward).
- The build takes about 20 s; the perturbation test rebuilds the pipeline twice more.

| Check | Result |
|---|---|
| Forbidden columns | None |
| As-of audit | 0 violations over 15 audited sources. The latest date used is exactly D − lag for every source (minimum margin 0 business days), so the lags are applied, not over-applied |
| Future perturbation (cutoff 2024-11-29) | 0 differing values in 81 features, in both `noise` and `drop` modes |
| Single-feature screen | Nothing above 0.95. The strongest single features are `cusip_fail_rate_250` (0.83), `qty_to_sl_avail` (0.83), `cusip_fail_rate_60` (0.81), `qty_to_adv20` (0.80) and `sl_total_avail_log` (0.80, inverse). All are proxies for the planted borrow hardness and size effects |
| Label shuffle | Holdout PR-AUC 0.0544 vs base rate 0.0540 (ROC-AUC 0.495): pass |

**Coverage below 100%** (share of rows non-missing, Beta / P3):

| Feature | Beta | P3 | Why |
|---|---|---|---|
| Box inventory (7 features) | 79% | 0% | Beta-only source; the synthetic box covers about 80% of CUSIPs |
| `confirm_seen`, `confirm_matched`, `confirm_affirmed` | 17% | 0% | Defined only for Beta DVP trades. See the lag caveat below |
| Loanet (5 features) | 99% | 99% | CUSIPs without a street position within the staleness window |
| `qty_to_cusip_open_fail_qty` | 14% | 15% | Defined only when the CUSIP has open fails |
| `cp_days_since_fail` | 99% | 94% | No earlier fail for the counterparty |
| `acct_days_since_fail` | 70% | 71% | No earlier fail for the account |
| `cusip_days_since_fail` | 76% | 76% | No earlier fail for the CUSIP |

**Caveats carried into phase 3:**
- **The affirmation lag hides same-day confirms.** Under T+1, a DVP trade's confirm is archived with `SnapshotDate` = T = D. The provisional lag of 1 hides it at scoring time, so only 343 Beta rows (about 6% of the DVP rows) see a confirm. This applies to both synthetic and live data. If P4 shows that `DailyArch_IDConfirms` lands before 22:30 ET on D, setting the lag to 0 makes this family informative. Otherwise affirmation stays a weak feature, and section 13 records the gap.
- **Deferred features** (catalog footer): the lagged DK count and counterparty open fails, because the counterparty ID spaces differ (Q9); SmartLoan buckets (P4); locates (`LocateSecurity` access); borrow needs (P3).

### 11.4 Phase 3: models, validation and calibration

**Status.** The code is done:
- `failmodel.models`: `baseline`, `validation`, `train`, `calibration`, `explain`, `metrics`, `bundle` and `report`.
- CLI `train [--tune] [--version]`. It writes the bundle and `reports/train_<version>.md`.
- `tests/test_model.py` (7): purge and fold order, top-K arithmetic, ECE and both calibrators, PSI, reason text, training, and a bundle round trip with scoring.

Live training waits on Q1.

**Synthetic results (synthetic data, not real results).** Bundle `fm-v1-20261014-synthetic`, trained with `--tune`:
- 6 monthly folds, October 2024 to March 2025.
- Holdout 2025-04-01 to 2025-06-30.
- Development training ends 2025-03-24 (5-business-day purge).

| Holdout | Model | PR-AUC | ROC-AUC | Base rate | ECE | Precision@5% | Recall@5% | Dollar recall@5% |
|---|---|---|---|---|---|---|---|---|
| Deliver | LightGBM (calibrated) | 0.424 | 0.868 | 6.16% | 0.0233 | 0.433 | 0.390 | 0.436 |
| Deliver | Logistic | 0.444 | 0.874 | 6.16% | n/a | 0.427 | 0.387 | 0.443 |
| Deliver | Rule | 0.175 | 0.772 | 6.16% | n/a | 0.232 | 0.207 | 0.361 |
| Receive | LightGBM (calibrated) | 0.094 | 0.775 | 2.79% | 0.0060 | 0.109 | 0.213 | 0.290 |
| Receive | Logistic | 0.093 | 0.776 | 2.79% | n/a | 0.119 | 0.232 | 0.308 |
| Receive | Rule | 0.051 | 0.680 | 2.79% | n/a | 0.051 | 0.104 | 0.179 |

| Acceptance | Deliver | Receive |
|---|---|---|
| Beats rule on PR-AUC | pass (0.424 vs 0.175) | pass (0.094 vs 0.051) |
| Beats rule on precision@50 | pass | pass |
| ECE ≤ 0.02 | **fail (0.0233)** | pass (0.0060) |
| Fold stability (min / mean fold PR-AUC ≥ 0.7) | pass (0.84) | pass (0.73) |
| Beats logistic (information) | no | yes (narrowly) |

What these results show:
- **Ranking is about 2.4× the rule** (deliver PR-AUC), and the main drivers are the planted ones:
  - Deliver: `qty_to_sl_avail`, `borrow_vwar` and the CUSIP fail rates (hardness and size).
  - Receive: `qty_to_sl_avail` and the counterparty and account fail rates (party quality).
- **Logistic matches LightGBM on synthetic data.** That is expected, because the generator is linear in log-odds (section 11.0). On live data, the folds decide whether LightGBM's non-linearity earns its place. If it does not, the logistic model is a valid, simpler v1.
- **The deliver ECE miss comes from base-rate drift, not ranking.** The synthetic deliver fail rate is 8–10% each month through April 2025, then 4.9% in May and 5.7% in June, because per-CUSIP hardness follows a persistent random walk. The calibrated mean score (6.4%) tracks the holdout base rate (6.2%), but the high-score bins over-predict. The untuned configuration landed at 0.0199. Choosing it because it passes would mean selecting on the holdout, so the tuned result stands as a fail. The response is operational:
  - the deployed calibrator is refit on the latest out-of-time predictions;
  - phase 4 monitors the calibration ratio and triggers recalibration (section 12).
- **Volume caveat.** The synthetic book has about 95 trades per direction per day, so K = 50 covers half a day's trades, and precision and recall at K = 25/50/100 say little here. Use the 5% metrics until real volumes and ops capacity (Q5) are known.
- **Fold early-stopping bias.** Early stopping uses each fold's validation month, so fold metrics are slightly optimistic. The holdout is not affected.

### 11.5 Phase 4: shadow scoring and monitoring

**Status.** The code is done:
- `failmodel.scoring`: population, completeness gate, masking of degraded sources, scoring and the per-day output writer.
- `failmodel.monitoring`: completeness, PSI drift, realized performance, alerts and go-live checks.
- `sql/scores/fail_prediction_scores.sql`: the Redshift score table, a latest-run view and a monitoring table.
- CLI `score`, `monitor [--report]` and `train --train-end`.
- `tests/test_scoring.py` (6 tests):
  - completeness statuses and roles;
  - masking of a degraded source on its own book only;
  - population, score columns and feature parity with the batch table;
  - the monitor's logs, rerun deduplication and go-live summary;
  - realized metrics with calibration and performance alerts;
  - a drift alert on a shifted top feature.

Live shadow runs wait on Q1.

**Synthetic shadow simulation (synthetic data, not real results).** The setup is out of sample:
- Bundle `fm-v1-shadow-synthetic` was trained with `--tune --train-end 2025-04-30`. Its holdout runs 2025-02-01 to 2025-04-30, and every acceptance gate passes.
- It then scored each business day from 2025-05-01 to 2025-06-27: 40 scoring dates, 7,656 trades.
- The monitor ran over the same dates. By the last day, 38 scoring dates had realized labels.

Results:
- **Training/serving parity.** All 7,656 shadow rows match the batch feature table on all 81 features. The scored population is exactly the batch population for those settle dates.
- **Completeness.** Every feature source passed on every day. `p3_rpe_fails` is reconciliation-only, so it is reported but never degrades a run (it is not in the synthetic test bed).

| Latest 20-day window (as of 2025-06-27) | Deliver | Receive |
|---|---|---|
| Rolling PR-AUC (holdout) | 0.314 (0.447) | 0.139 (0.096) |
| Ratio to holdout | **0.70** (needs ≥ 0.8) | 1.45 |
| Precision@50 (holdout) | 0.095 (0.149) | 0.048 (0.044) |
| Calibration ratio, predicted / observed fails | 1.25 (peak 1.54) | 0.82 |
| Score PSI over 20 pooled days, median (max) | 0.097 (0.162) | 0.043 (0.093) |

| Alert | First raised | Scope | Days raised |
|---|---|---|---|
| Feature drift | 2025-05-07 | Deliver `book_open_fails_same_dir_log` | 36 |
| Feature drift | 2025-05-07 | Receive `acct_fail_rate_250` | 36 |
| Calibration | 2025-05-13 | Deliver | 25 |
| Performance (critical) | 2025-06-12 | Deliver | 11 |
| Feature drift | 2025-06-13 | Deliver `acct_days_since_fail` | 10 |
| Feature drift | 2025-06-25 | Receive `cp_fail_rate_20` | 3 |

What the simulation shows:
- **The monitor caught the planted regime change before performance fell.**
  - The synthetic deliver fail rate drops from 8–10% to about 5% in May 2025 (section 11.4).
  - Drift came first. Book-level open deliver fails fell from about 20 to about 12, and feature PSI rose to about 5.8.
  - Calibration over-prediction followed six days later.
  - The critical PR-AUC alert came a month after that. Part of the drop is prevalence: PR-AUC falls when the base rate halves, even with ranking unchanged.
- **Go-live is correctly blocked for deliver.** It fails the PR-AUC and precision@50 checks and still has an open critical alert. Receive passes on performance, but its outperformance flag asks for a review. The response is the one section 12 prescribes: recalibrate on recent out-of-time predictions, or retrain early.
- **Precision@50 is a weak signal at synthetic volumes.** About 95 deliver trades are scored per day, so K = 50 is half the day's book (section 11.4).

### 11.6 Phase 5: fail persistence and close-out risk

**Status.** The code is done:
- `failmodel.persistence`: fail tracking, labels, reconciliation, fail-state and cure-history features, the age-only benchmark and training (section 3.2).
- `failmodel.models.train` now takes a `Task` (target, features, benchmark, date column, slices). The new-fail model is the default task, and its holdout results are unchanged (deliver PR-AUC 0.4474, receive 0.0963).
- CLI `persistence [--task ...] [--tune]` writes `processed/persistence.parquet`, `reports/persistence_reconciliation.csv` and `reports/persistence_<date>.md`.
- `sql/persistence/01_fail_charge_profile.sql`: read-only profiling of the severity target, for when `RiskCredit` and `NSCC` can be read.
- `tests/test_persistence.py` (6 tests):
  - tracking labels on a hand-built history;
  - the reconciliation identities;
  - close-out labels;
  - the future-perturbation test with its negative control (section 7);
  - the age-only benchmark;
  - training on the phase-3 engine.

The full suite (51 tests) passes. Live runs wait on Q1; the severity target waits on Q6.

**Synthetic results (synthetic data, not real results).** This is the official run, `persistence --tune`. As in phase 3, the tuned configuration stands; a configuration is never chosen by its holdout score.

*Rows.* The run tracked 13,384 fail-snapshot rows. 1,548 have no eligible trade (DEC-16) and are excluded:
- 990 Beta zero-control fails with no fallback match (non-trade activity);
- 468 P3 non-trade record types;
- 77 on trades outside the v1 product scope;
- 13 on trades entered after the snapshot.

From 2024-05-28 there are 8,537 modelling rows: Beta deliver 4,472, Beta receive 1,124, P3 deliver 2,357, P3 receive 584. Of these, 836 come from trades entered after S − 1. v1 does not score those trades, but their fails are known by F.

| Label | Labelled rows | Rate |
|---|---|---|
| `still_open_h1` | 8,457 | 61.7% |
| `still_open_h3` | 8,408 | 27.2% |
| `still_open_h5` | 8,377 | 13.6% |
| `reg204_breach` (deliver equity) | 822 | 38.6% |
| `muni_breach` | 2,100 | 14.1% |

*Reconciliation.* The persistence labels agree with snapshot counts on every consecutive snapshot pair (369 Beta, 371 P3):
- open fails on F = still open on F + 1 + cured;
- rows on F + 1 = carried + new + reappeared.

No fail keys collide within a snapshot, and no key leaves and later reappears. The synthetic keys are stable by construction, so on live data the reappeared share is the first check of `FailUniqueId` stability (Q12).

*Holdout* (last 3 months, by snapshot date):

| Task | Direction | Rows | Base rate | LightGBM PR-AUC | Age-only | Logistic | ECE | Fold stability |
|---|---|---|---|---|---|---|---|---|
| `still_open_h1` | D | 1,109 | 0.626 | 0.732 | 0.659 | 0.763 | 0.033 | 0.94 |
| `still_open_h1` | R | 364 | 0.503 | 0.607 | 0.549 | 0.545 | 0.067 | 0.91 |
| `still_open_h3` | D | 1,080 | 0.282 | **0.490** | 0.342 | 0.517 | 0.049 | 0.67 |
| `still_open_h3` | R | 355 | 0.169 | **0.240** | 0.191 | 0.193 | 0.099 | 0.53 |
| `still_open_h5` | D | 1,051 | 0.145 | 0.354 | 0.196 | 0.329 | 0.053 | 0.52 |
| `still_open_h5` | R | 342 | 0.064 | 0.075 | 0.074 | 0.080 | 0.062 | 0.33 |
| `reg204_breach` | D | 107 | 0.364 | 0.569 | 0.622 | 0.497 | 0.126 | 0.48 |
| `muni_breach` | D | 291 | 0.192 | 0.483 | 0.464 | 0.489 | 0.121 | 0.32 |

Two tasks were not trained. `reg204_breach` applies to deliver fails only, and receive `muni_breach` has 9 positives, below the minimum of 50 per class.

What the results show:
- **The exit criterion is met.** `still_open_h3` beats age-only in both directions: deliver 0.490 vs 0.342, receive 0.240 vs 0.191. So do h1 in both directions and h5 deliver.
- **The drivers are the planted ones.** For deliver h3 they are borrow difficulty (`threshold_flag`, `borrow_vwar`, `loanet_rebate`) and counterparty quality (`cp_fail_rate_250`, `fail_cp_persist_rate_h1`). Receive h3 leans on counterparty and account fail rates. It also uses book-level series (`book_persist_rate_h3`, `global_fail_rate_250`), which track the period more than the fail; with 355 holdout rows that deserves a check on live data.
- **Receive h5 has no signal at this size.** The holdout has 22 positives, and LightGBM ties age-only (0.075 vs 0.074).
- **The close-out models are not fit for use yet.**
  - `reg204_breach` loses to age-only (0.569 vs 0.622) on 107 holdout rows.
  - `muni_breach` beats it narrowly (0.483 vs 0.464) on 56 positives, which is within noise.
  - For both, a fail's age almost fixes the business days left to the deadline, so age-only is a strong benchmark. Both are revisited on live volumes, and neither is shown to ops until it clearly beats age-only.
- **The phase-3 gates are reported, but they are not the phase-5 gate** (DEC-19).
  - Every task misses ECE ≤ 0.02 (0.033 to 0.126). At these sizes that mostly reflects sample size. For example, the deliver h3 holdout has about 108 rows per calibration bin at a 28% rate, so each bin's observed rate has a standard error of about 0.043, and a perfectly calibrated model would still show an ECE near 0.035.
  - Fold stability is below 0.7 for h3, h5 and the close-out tasks. The folds are monthly slices of a few hundred rows.
  - Both gates are re-applied on live volumes.
- **Logistic matches or beats LightGBM on several tasks** (h1 and h3 deliver). As in phase 3, that is expected from the linear synthetic generator (section 11.0).

## 12. Deployment and monitoring

The plan is [plans/06-phase4-shadow-scoring.md](plans/06-phase4-shadow-scoring.md). Results on synthetic data are in section 11.5.

### 12.1 Nightly run (D = today, 22:30 ET)

```
python -m failmodel.cli extract --start D --end D      # 18 wrapper calls
python -m failmodel.cli score   --date D               # latest bundle, or --bundle PATH
python -m failmodel.cli monitor --date D [--report]
```

1. **Extract.** The daily incremental is 18 wrapper calls, one per active source in `failmodel.sources`.
   - 17 calls feed scoring. `p3_rpe_fails` is reconciliation-only, and `business_calendar` can refresh monthly.
   - The 3 conditional procedures (`stock_loan_borrow`, `borrow_for_needs`, `locates`) are added only if profiling or access enables them. That brings the total to the 21 procedures in the WSI request.
   - Plan 06's figure of 16 calls predates `p3_eod_prices` (v0.2) and `p3_rpe_fails` (v0.4).
2. **Completeness gate** (`monitoring.completeness`). Each source is checked on its required as-of date, `D − availability lag`, and gets a role:

   | Role | Sources | Check | If it fails |
   |---|---|---|---|
   | `trade` | `beta_trades`, `p3_trades`, `business_calendar` | Rows on D within ±50% of the 20-day median | Critical alert; the scores for that book are unreliable |
   | `feature`, banded | Inventory, market, CNS, SmartLoan, Loanet, borrow, security, prices | Rows on the required date within ±50% of the 20-day median | Source degraded: its features are set to missing on its own books only (`mask_degraded`); warning alert; run continues |
   | `feature`, fail snapshots | `beta_fail_snapshots`, `p3_fail_snapshots` | The required date's snapshot exists | As above. The row count is the outcome itself, so it is not banded |
   | `feature`, event-style | `beta_threshold_history`, `reg204`, `beta_id_confirms` | Any rows within the staleness window | As above |
   | `reconciliation` | `p3_rpe_fails` | Reported only | No alert; never degrades a run |

   LightGBM handles missing values natively, so a degraded source lowers sharpness but does not stop the run. The history features are derived from labels, not from a raw source, so they are never masked. A missing label snapshot leaves them one day staler.
3. **Population.** Every trade with S = the next business day after D that passes the phase-1 rules, including entered by D (section 4).
   - In a backfill, the cancellation flag reflects the extract's final status, so trades cancelled after D are already excluded.
   - In a live 22:30 run they are scored. They then appear as `unlabelled` in the realized log rather than as non-fails.
4. **Score** (`ModelBundle.score`). Calibrated probability, raw score, rank within (scoring date, book, direction), and three plain-language reason codes.
5. **Write.** `artifacts/scores/score_date=D/` holds:
   - `scores.parquet` (the score table rows);
   - `features.parquet` (the feature snapshot, for drift and audit);
   - `completeness.csv`;
   - `run.json` (run id, model version, counts by book and direction, degraded sources and masked features).
   In production, `scores.parquet` is loaded into `ops.fail_prediction_scores`.

A backfill (`score --date D1 --through D2`) truncates every extract to what was known on D2. It builds one context and scores each D. The future-perturbation test (section 7) is what makes each D's features identical to a run on D alone. Section 11.5 confirms exact parity with the batch table.

### 12.2 Score table

`sql/scores/fail_prediction_scores.sql` (Redshift):
- **`ops.fail_prediction_scores`.** Primary key (`scoring_date`, `model_version`, `trade_key`), `DISTKEY (settle_date)`, `SORTKEY (scoring_date, book, direction, risk_rank)`. It carries `fail_probability`, `raw_score`, `risk_rank`, three reasons (code, text, SHAP contribution), `degraded_sources` and `shadow_mode`.
- **`ops.v_fail_prediction_scores_latest`.** The latest run per scoring date and model version, so reruns replace earlier runs.
- **`ops.fail_prediction_monitoring`.** Daily realized performance.

No PII is written. The Ops Hub reads the latest view through `queryAnalyticsWarehouse()`. The table must be registered in the Ops Hub's `docs/integrations/data-sources.yaml`.

### 12.3 Monitoring

The monitor runs after scoring. On monitoring day M, the label for S is known only when S ≤ M − 1 (the Beta snapshot lands on S + 1). Scores made on D are therefore evaluated from D + 2. Outputs go to `artifacts/monitoring/`, upserted so that reruns replace rows:
- `performance_daily.csv`
- `rolling.csv`
- `alerts.csv`
- `score_drift.csv`
- `feature_drift_top.csv`

| Signal | Metric | Alert | Severity |
|---|---|---|---|
| Completeness | Section 12.1 gate | Trade source missing or out of band; feature source degraded | Critical (trade) / warning |
| Feature drift | PSI of each top-20 feature (from the bundle's importance) against the deployed model's training rows, over the trailing 20 scoring days pooled (from 5 days) | PSI > 0.25 | Warning |
| Score drift | PSI of calibrated scores against holdout scores, same pooled window | PSI > 0.2 | Warning |
| Realized performance | Rolling 20-day PR-AUC per direction (pooled across books); daily precision@50 | Rolling PR-AUC < 80% of holdout, if ≥ 20 fails in the window | Critical |
| Calibration | Rolling 20-day predicted / observed fails | Outside 0.7 to 1.3, if ≥ 20 fails | Warning |

PSI pools trading days for two reasons (DEC-13):
- One day is a few hundred trades per direction, so sampling noise alone approaches the threshold.
- Many features, such as book-level open fails and the global fail rate, are constant within a book-day.

On synthetic data, single-day PSI raised 569 drift alerts in 40 days, mostly noise. Pooling left 85, all on features that really moved.

### 12.4 Go-live criteria

`monitor --report` writes `reports/shadow_<start>_<end>.md` with these checks (`monitoring.shadow_summary`):

| Check | Rule |
|---|---|
| `shadow_days_ok` | At least 30 scoring days with realized labels |
| `no_open_critical_ok` | No critical alert on the latest monitoring day |
| `<dir>_pr_auc_ok` | Latest rolling PR-AUC ≥ 80% of holdout |
| `<dir>_precision_at_50_ok` | Latest rolling precision@50 ≥ 80% of holdout |
| `info_<dir>_review_outperformance` | Either ratio above 120%; review for a label or population change. Not a gate |
| Manual | Ops review of top-50 lists and reasons; integration path agreed with the WTS data team; retraining cadence agreed |

"Within 20% of the holdout" in plan 06 is applied one-sided (DEC-14). Performance well above holdout is not a reason to block go-live, but it is flagged for review because it can signal a label or population change.

**Response to alerts.**
- A calibration alert alone means recalibrating: refit the calibrator on the latest out-of-time scores and labels.
- A performance alert, or persistent drift on top features, means retraining early.
- Otherwise, retrain quarterly.
- Every new bundle repeats the phase-3 acceptance gates and starts a new shadow period before it replaces the active version.

## 13. Limitations and model risk

- **Legacy-book training.** v1 learns Beta and P3 behavior. Broadridge migration will shift the distribution, so the model must be re-validated before it is used on Broadridge trades.
- **Asymmetric features.** Box inventory and affirmation features are Beta-only until `ReplicateDB_Archive` is granted.
- **Affirmation is mostly invisible at a 1-day lag.** Under T+1, same-day confirms are archived on D, so with the provisional lag only about 6% of Beta DVP rows see their confirm (section 11.3). Profiling check P4 decides whether the lag can be 0.
- **Security reference comes from Beta.** P3 trades in CUSIPs missing from Beta's security master get no security, liquidity or threshold features.
- **Calibration drifts with the base rate.** Fail rates move with market-wide borrow conditions. A calibrator fitted on past months over-predicts after a drop, as the synthetic deliver holdout shows (section 11.4). Rankings hold up better than probabilities, so ops should use the rank and reasons first. Phase-4 monitoring watches the calibration ratio.
- **The model class is not yet proven.** On synthetic data the logistic benchmark matches LightGBM. Live folds must show that LightGBM's added complexity pays off before go-live.
- **Labels depend on snapshot timing.** If a snapshot is taken before late settlements post, some trades are labelled as fails although they settled late that day. Profiling check P2 quantifies this.
- **Partial fails** count as fails. Quantity fractions are kept for analysis.
- **Non-trade fails** (transfers, stock-loan returns) are outside the population, so the model does not cover them. The same holds for phase 5, which models only fails that come from an eligible trade.
- **Phase-5 close-out labels are approximations** (DEC-17):
  - Rule 204 uses S + 3 for every deliver equity fail, because no short-sale marker has been found (Q11). For short sales, which have an S + 1 deadline, the label is too lenient.
  - The labels are per trade fail, while Rule 204 applies to the firm's net CNS position in the CUSIP.
  - They flag fails that reach the deadline unresolved. They are not a regulatory determination.
- **The phase-5 close-out models do not yet beat their benchmark.** On synthetic data they lose to, or barely beat, age-only on small holdouts (section 11.6). They stay unused until live volumes show a clear gain.
- **Re-anchored trade-timing features change meaning in phase 5.** With the scoring date set to F, features such as `book_trades_entered_on_d_log` describe the snapshot day, not the day before settlement. The model learns them as such, but their v1 descriptions do not apply.
- **Fail tracking assumes stable fail keys.** If Beta `FailUniqueId` or the P3 key changes during a fail's life, the fail appears cured and a new one opens, which understates persistence. The reconciliation's reappeared share and new-fail counts are the check (Q12).
- **The severity target is blocked.** Neither `RiskCredit` nor `NSCC` is reachable through the wrapper (section 5.5), and charges are per CUSIP, not per fail.
- **Governance:**
  - The model is advisory only.
  - Changes are versioned in this document.
  - Independent review comes before go-live.
  - Monitoring thresholds trigger re-validation.
  - These practices are aligned with SR 11-7 model-risk principles.

## 14. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| Q1 | Will WSI deploy and allowlist the 21 extract procedures (request sent as [requests/wsi-data-access-request.md](requests/wsi-data-access-request.md)), and can the wrapper's `OperationsPortalDB` login read `BetaAccessArchive` and `NSCC`? | WSI data / DB Wrapper owners | Phase 0 |
| Q2 | When is the Beta `fal` file loaded? Which business day does `SnapShotDate` represent? | WSI Beta ops | Phase 0 (P2) |
| Q3 | How many P3 `TRDDTL_ARCH` rows exist per trade key, and which row is authoritative? | WSI P3 / profiling | Phase 0 (P5) |
| Q4 | Can `LocateSecurity`, `Intranet` and `ReplicateDB_Archive` be granted? | WSI DBA | Phase 0 |
| Q5 | What daily review capacity (K) does ops have, per direction? | Ops managers | Phase 3 |
| Q6 | How long is `nsccintradaycnsfailspositions` retained, and how can the model read it (a read-only PostgreSQL login, or a daily export)? Can `NSCC.dbo.FailsDetailReport` be read as the fallback? The profiling SQL is ready (`sql/persistence/01_fail_charge_profile.sql`) | RiskCredit owners / WSI DBA | Phase 5 |
| Q7 | When will Broadridge settlement-obligation fail status be available (Ops Hub D-02)? | WTS data team | Broadridge transition |
| Q8 | Which P3 `EODFAIMST.RECORD_TYPE` codes come from trades, and which from transfers, loans or other non-trade activity? | WSI P3 / profiling P5 | Phase 1 |
| Q9 | Is there a mapping between Beta fail `NSCCNumber` and trade `ContraBrokerNumber`/`ContraDealerNumber`? It would let the lagged DK count and counterparty open fails become features | WSI Beta ops | Phase 2 (deferred features) |
| Q10 | Who runs the nightly job (scheduler and host), and which role loads `ops.fail_prediction_scores` in Redshift? The table must also be registered in the Ops Hub `data-sources.yaml` | WTS data team / Ops Hub owners | Go-live |
| Q11 | Which field marks short sales on Beta and P3 trades? Beta `Trade.short_sale_review_code` is the only candidate in the mirrored DDL, and `TRDDTL_ARCH` has none. With it, `reg204_breach` can use the S + 1 short-sale deadline | WSI Beta and P3 ops | Phase 5 |
| Q12 | Is Beta `FailUniqueId` (and the P3 trade key plus record type) stable for the whole life of a fail, including partial cures and reason-code changes? Check it with the reappeared share in `reports/persistence_reconciliation.csv` on live data | WSI Beta ops / profiling | Phase 5, live |

## 15. Decisions log

| # | Date | Decision | Rationale |
|---|---|---|---|
| DEC-1 | 2026-10-14 | v1 is a trade-level new-fail model; persistence becomes phase 5 | The goal is to act before the fail. The CUSIP-level "still open" framing predicts persistence, not new fails |
| DEC-2 | 2026-10-14 | Train on Beta and P3, pooled, with a `book` feature | Most labelled history; one canonical schema eases the later Broadridge onboarding |
| DEC-3 | 2026-10-14 | Separate deliver and receive models | Different drivers: inventory and borrow for deliver, counterparty behavior for receive |
| DEC-4 | 2026-10-14 | Score at about 22:30 ET on S-1 | After the affirmation cutoff and the P3 end-of-day snapshot, but before overnight actions |
| DEC-5 | 2026-10-14 | Labels start 2024-05-28; extracts start 2023-05-01 | T+1 regime only; one year of warm-up for 250-business-day history features |
| DEC-6 | 2026-10-14 | All project files live under `Model/`; numbered plans are written before building | User instruction |
| DEC-7 | 2026-10-14 | Extract procedures live in `OperationsPortalDB` and read other databases by three-part name; adding wrapper databases is the fallback | No wrapper code change needed; the allowlisted fail-analysis procedure already reads `BetaDailyArchive` this way |
| DEC-8 | 2026-10-14 | Availability lags default to 1 business day until profiling confirms landing times | A lag that is too long costs a little signal; one that is too short leaks |
| DEC-9 | 2026-10-14 | Validate every phase on a synthetic test bed in the native extract layout while access is pending | The code is tested end to end against planted structure; when access is granted, only the data changes |
| DEC-10 | 2026-10-14 | P3 labels come from `EODFAIMST` for all dates; the RPE200/RPE201 files (the report's source from 2025-08-14) are extracted for reconciliation only. If P8 finds less than 95% of RPE Age-1 keys in `EODFAIMST`, the post-switch label source is reviewed | One label definition across the whole window; RPE rows lack `FIRM_NBR` and `CCURRENCY`, so they cannot carry the 7-part trade key |
| DEC-11 | 2026-10-14 | Age-1 reconciliation counts P3 non-trade record types and Beta zero-control fails without a fallback match as non-trade, not as join failures | They have no originating trade; counting them would understate the join quality the 90% criterion measures |
| DEC-12 | 2026-10-14 | Fail snapshots, CNS positions and Rule 204 are joined on the source's latest snapshot date (a missing key is 0), not on each key's last row | A key missing from a snapshot means no open fail or no position. Carrying the last row forward would revive cured fails for up to `max_staleness_bd` days |
| DEC-13 | 2026-10-14 | Feature and score PSI pool the trailing 20 scoring days (alerts from 5 days); fail-snapshot completeness is a presence check, not a row-count band | One day's sample is too small, and many features are constant per book-day: single-day PSI gave 569 synthetic alerts, mostly noise. A fail list's size is the outcome, not a load-quality signal |
| DEC-14 | 2026-10-14 | Go-live performance checks are one-sided (rolling ≥ 80% of holdout); outperformance above 120% is an informational review flag | Better-than-holdout performance should not block go-live, but it can signal a label or population change |
| DEC-15 | 2026-10-14 | Fail state (age, quantity, reason, condition, DK count, `FailWho`) is a feature only in the persistence model, taken from snapshot F and earlier. Cure-history rates count only fails whose horizon closed by F | The fail already exists at F, so its state is known then. The v1 model still forbids these fields (section 7, rule 1) |
| DEC-16 | 2026-10-14 | Persistence rows are fails from trades that pass the v1 population rules, with entry by F in place of entry by S − 1. The match is by exact trade key, or else by a unique fallback-key match on the fail's first snapshot. Other fails are counted but not modelled | The re-anchored features need the originating trade, and non-trade fails are outside scope. A trade entered late is known by the time its fail is open, so excluding it would drop real fails (836 synthetic rows) |
| DEC-17 | 2026-10-14 | Close-out labels: `reg204_breach` means a deliver equity fail is still on the end-of-day snapshot for S + 3. `muni_breach` means a municipal fail in either direction is still open on S + 10. Both are labelled only for rows before the deadline, per trade fail | No short-sale marker has been found (Q11), and the labels are review flags, not regulatory determinations. Rows on or after the deadline have nothing left to predict |
| DEC-18 | 2026-10-14 | Persistence training widens the train/validation purge to max(5, label horizon) business days; for close-out tasks the horizon is the largest number of days to the deadline | A label at F + h would otherwise overlap the first validation rows |
| DEC-19 | 2026-10-14 | The phase-5 gate is plan 07's: beat age-only on holdout PR-AUC. The phase-3 ECE and fold-stability gates are reported and re-applied on live volumes | Synthetic holdouts of 100 to 1,100 rows put 10-bin ECE noise near 0.035, above the 0.02 gate, even for a calibrated model |

---

## Appendix A — Project layout

```
Model/
  failanalysispredictivemodel.md      this document
  project-overview-and-handover.md    plain-language overview and handover guide
  docs/                               make_figures.py and the overview's charts
  plans/                              00 to 07 phase plans
  requests/                           data-access requests
  sql/                                extract, profiling, label, score and persistence SQL
  src/failmodel/                      Python package
  tests/                              unit and leakage tests
  notebooks/                          exploration
  data/  artifacts/                   git-ignored extracts and model files
```

## Appendix B — Glossary

| Term | Meaning |
|---|---|
| S, D | Contractual settle date; scoring date (the business day before S) |
| Age 1 | A fail on its first day: still open at the end of S. Age is counted in calendar days in the report, and in business days in this model |
| CNS | NSCC Continuous Net Settlement. `ClosingNetPosition` < 0 is a fail to deliver |
| Rule 204 | The Reg SHO close-out requirement for CNS fails to deliver |
| DVP/RVP | Delivery or receipt versus payment (institutional settlement) |
| PSI | Population stability index, a drift metric |
| PR-AUC | Area under the precision–recall curve |
