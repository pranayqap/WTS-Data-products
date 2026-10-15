# WSI Data Access Request: Fail Prediction Model

- **Requested by:** Data & Decision Science (Fail Analysis predictive model)
- **Status:** Draft, ready to send
- **Related:** [`../failanalysispredictivemodel.md`](../failanalysispredictivemodel.md), [`../plans/01-phase0-data-access.md`](../plans/01-phase0-data-access.md), proposed SQL in [`../sql/extract/`](../sql/extract/)

## 1. Summary

We are building an advisory model that scores, each evening, which trades settling on the next business day are likely to fail. It adds a ranked list to the Ops Hub Fail Analysis tool. It does not take any automated action.

The model needs about three years of point-in-time history from WSI databases. We want to read it through the WSI DB Wrapper, so the WSI/WTS entity boundary stays where it is.

The wrapper can only connect to eight logical databases today: `OperationsPortalDB`, `ReportDB`, `Phase3DB`, `P3ClearingDB`, `BetaAccess`, `ReplicateDB`, `ControlDB` and `WedbushClient`. That list is fixed in `DatabaseConnectionResolver.cs`. Adding a database is a code change: a new resolver entry and connection-string property, a config value, and a redeploy.

We are asking for four things:

1. **Deploy 21 read-only extract procedures** in `OperationsPortalDB` and add them to the `WSIDBWrapperSPList` allowlist (section 3).
2. **Cross-database read** from the wrapper's `OperationsPortalDB` login to `BetaDailyArchive`, `BetaAccessArchive` and `NSCC`. Alternatively, add those three as wrapper databases (section 4, group A).
3. **Add three unmirrored databases**: `ReplicateDB_Archive`, `LocateSecurity` and `Intranet` (section 4, group B).
4. **Provide a path to PostgreSQL `RiskCredit`.** This is only needed for the phase-5 severity model (section 4, group C).

## 2. Data-handling commitments

- The extracts are read-only and only cover dated or archived tables. Nothing writes back to WSI databases. The only write is the one-time allowlist registration.
- No PII is extracted: no `SSNTIN` and no customer or account names. Account numbers are kept because joins and counterparty history need them.
- Extracts are stored as Parquet under `Model/data/raw/`. That folder is git-ignored and sits on an encrypted disk.
- The output is a daily score table plus reason codes. The model is advisory only.

## 3. Request 1: extract procedures on the allowlist

The procedures live in `OperationsPortalDB.dbo`, alongside `GetBetaFailAnalysisData_Report` and `GetP3FailAnalysisData_Report`. Those procedures are already allowlisted and already read other databases from there.

Every extract procedure has the same signature: `@FromDate DATE, @ToDate DATE, @RowCount INT OUTPUT`. Each call is limited to a 31-day window so it stays well inside the wrapper's 300-second timeout. Each procedure returns one result set, ordered on its as-of key.

The column lists were checked against the mirrored table definitions.

| Procedure (`usp_FailModel_Extract_…`) | Reads | As-of key | Purpose |
|---|---|---|---|
| `BetaTrades` | `BetaAccess.dbo.Trade` ∪ `BetaAccessArchive.dbo.Trade` | `TradeDate` | Beta trade universe (label negatives) |
| `BetaFailSnapshots` | `BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo` | `SnapShotDate` | Beta label, fail pressure |
| `BetaSecurity` | `BetaDailyArchive.dbo.DailyArch_Security` | `SnapShotDate` | Security reference, chills, eligibility, bid/ask |
| `BetaSegregation` | `BetaDailyArchive.dbo.DailyArch_SegregationAnalysisInfo` | `SnapShotDate` | Box inventory and deficit |
| `BetaCloseHistory` | `BetaAccessArchive.dbo.SecurityCloseHistory` | `TradeDate` | Close price and volume |
| `BetaIDConfirms` | `BetaDailyArchive.dbo.DailyArch_IDConfirms` | `SnapshotDate` | Institutional match and affirm state |
| `BetaThresholdHistory` | `BetaAccess.dbo.SecurityExtensionHistory` | `EffectiveDate` / `AddedTimestamp` | Reg SHO threshold history |
| `P3Trades` | `ReplicateDB.dbo.TRDDTL_REPL` ∪ `Phase3DB.dbo.TRDDTL_ARCH` | `TRADE_FULL_DATE` | P3 trade universe (label negatives) |
| `P3FailSnapshots` | `Phase3DB.dbo.EODFAIMST` | `SnapShotDate` | P3 label, fail pressure |
| `P3RpeFails` | `Phase3DB.dbo.RPE200` ∪ `RPE201` (no `AccountName`, `RepCode`) | `SnapShotDate` | Report-side P3 fails from 2025-08-14, for label reconciliation only |
| `P3EodPrices` | `ReplicateDB.dbo.EODCMVDAT_REPL` | `SnapShotDate` | P3 point-in-time price |
| `CnsP3` | `Phase3DB.dbo.CNS_8199_S1_Data` | `SettleDate` | P3 CNS positions |
| `CnsBeta` | `NSCC.dbo.CNS_S1_Data` | `TradeDate` | Beta CNS positions |
| `SmartLoanAvail` | `ReportDB.dbo.SmartLoanSLAvail` | `FileDate` | Lendable supply |
| `LoanetPositions` | `ReportDB.dbo.LoanetOpenPositions` (aggregated) | `TradeDate` | Street borrow/loan tightness |
| `BorrowRates` | `ReportDB.dbo.StockBorrowInterestRate` | `FileDate` | Borrow cost |
| `Reg204` | `ReportDB.dbo.CNS204FailSettlementData` | `ReportDate` | Published Reg SHO 204 grid |
| `BusinessCalendar` | `ControlDB.dbo.DateMaster` | `ActualDate` | Business calendar, month and quarter end |
| `StockLoanBorrow` (conditional) | `NSCC.dbo.StockLoanBorrow` | `SettleDate` | Borrow position. Used only if run timing passes profiling |
| `BorrowForNeeds` (conditional) | `NSCC.dbo.BorrowForNeeds` | `SettleDate` | Borrow needs. Used only if run timing passes profiling |
| `Locates` | `ReportDB.dbo.ShortSellStockLocate_Archive` (aggregated) | `TradeDate` | Short-sale locate demand |

The deployment scripts are in this order:

- `00_common.sql`: the CUSIP universe function and a window guard.
- `01`–`04`: the procedures.
- `05_allowlist_registration.sql`: idempotent `WSIDBWrapperSPList` inserts, followed by `POST /wsi-db-wrapper/stored-procs/allowlist/refresh`.

### Volume and pacing

| Load | Calls | Time at the wrapper's 100 calls/minute limit |
|---|---|---|
| Backfill, 2023-05-01 to present (about 870 business days) | About 14,000 if every source is pulled one day per call. About 800 if light sources are pulled monthly | About 2.5 hours worst case. Our client paces itself at 90 calls/minute and honours `Retry-After` |
| Daily incremental | 21 | Under 1 minute |

If WSI would rather keep a bulk backfill off the wrapper, a one-time read-only replica login (or a WSI-run export of the same procedures) works too. The daily run would still use the wrapper.

## 4. Databases the wrapper cannot reach

These databases are needed but sit outside the wrapper's eight-database list. They are grouped by how they could be opened up.

### Group A: on the same server as `OperationsPortalDB`, readable through hosted procedures

The cheapest route is to read these through the `OperationsPortalDB` procedures in section 3. That needs no wrapper code change, provided the wrapper's `OperationsPortalDB` login can read them, through cross-database ownership chaining or `db_datareader`. If WSI won't allow cross-database reads, please add them as wrapper databases instead.

| Database | Tables | Model use | Evidence it can be read through `OperationsPortalDB` |
|---|---|---|---|
| `BetaDailyArchive` | `DailyArch_OpenFailReceiveDeliverInfo`, `DailyArch_Security`, `DailyArch_SegregationAnalysisInfo`, `DailyArch_IDConfirms` | Beta label, security reference, box inventory, affirmations | Yes. The allowlisted `GetBetaFailAnalysisData_Report` already reads `BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo` |
| `BetaAccessArchive` | `Trade`, `SecurityCloseHistory` | Beta trade history older than the live table keeps; close price and volume | No allowlisted procedure reads it today, so the permission needs confirming |
| `NSCC` | `CNS_S1_Data`; conditional `StockLoanBorrow`, `BorrowForNeeds`; phase 5 `FailsDetailReport` | Beta CNS positions, borrow position, fail charges | No allowlisted procedure reads it today, so the permission needs confirming |

### Group B: not in the schema mirror; grants and a wrapper route are needed

| Database | Tables | Model use | Impact if declined |
|---|---|---|---|
| `ReplicateDB_Archive` | `PHASE3_TRDDTL_REPL`, `FAIMST_AUDIT_REPL`, archived `EODSTKREC_REPL` / `EODNMADDA_REPL` | Older P3 trade detail (label negatives before `TRDDTL_REPL` / `TRDDTL_ARCH` coverage starts); P3 fail change history | Fewer P3 training months if `TRDDTL_ARCH` coverage is short. Profiling check P1 will measure this |
| `LocateSecurity` | `EasyToBorrowListVersioned`, `ETBAnalysis` | Hard-to-borrow / easy-to-borrow status history | The borrow-difficulty feature falls back to Loanet rebates and borrow rates, a weaker proxy |
| `Intranet` | `BorrowSecurities`, `BorrowStatus` | Borrow status | Same fallback as `LocateSecurity` |

### Group C: different platform

| Database | Table | Model use | Note |
|---|---|---|---|
| PostgreSQL `RiskCredit` | `nsccintradaycnsfailspositions` | Phase 5 severity target (`final_fails_charge`) | The wrapper only connects to SQL Server. Options: a PostgreSQL-backed endpoint, a daily export into a SQL Server table the wrapper can reach, or a read-only login. Not needed for v1; the fallback is `NSCC.dbo.FailsDetailReport.CNSFailCharge` |

### Optional

| Database | Use | Alternative |
|---|---|---|
| `msdb` | Job history, to confirm when the `DailyArch_*`, `StockLoanBorrow` and `BorrowForNeeds` loads finish relative to the 22:30 ET scoring time | A one-time export of the relevant job schedules and run times from WSI |
| `HistoryDb` | `BuyInCalcs_930PM`, phase 5 buy-in context only | Not needed for v1 |

## 5. Questions for WSI subject-matter experts

1. **`EODSTKREC_REPL.LOC_LOCATION`**: which location codes are firm box, segregation, and DTC free versus pledged?
2. **`NSCC.dbo.CNS_S1_Data.TradeDate`**: is it the trade date or the CNS report/settle date?
3. **`StockLoanBorrow` and `BorrowForNeeds`**: when do they run, and is a past `SettleDate` ever recomputed? They have no load-timestamp column, so we cannot tell from the data.
4. **`DailyArch_OpenFailReceiveDeliverInfo.SnapShotDate`**: is it the business date the fails were open on, or the next calendar day when the archive job ran?
5. **`Phase3DB.dbo.EODFAIMST` after the 2025-08-14 switch to the RPE200/RPE201 files.**
   - Is `EODFAIMST` still loaded in full each evening?
   - Should every Age-1 fail in the RPE files also appear in `EODFAIMST`? Matching is on account, `TRAN_NBR`, trade date, market and CUSIP, the same join the combined view uses.
   - `GetP3FailAnalysisData_Summary` reads the RPE view for dates before the switch as well. Is that intended?

   We label from `EODFAIMST` and measure the coverage in profiling check P8.
6. **`Phase3DB.dbo.TRDDTL_ARCH`**: no loader is in the mirror. What populates it, and from what date?
7. **Short-sale marking (phase 5).** Which field marks a short sale on Beta and P3 trades? Beta `Trade.short_sale_review_code` is the only candidate in the mirrored DDL, and `TRDDTL_ARCH` has none. Rule 204 closes out short sales by S+1 rather than S+3.
8. **Fail-key stability (phase 5).** Does Beta `FailUniqueId` stay the same for the whole life of a fail, through partial cures and reason-code changes? Does the P3 trade key plus `RECORD_TYPE` stay the same in `EODFAIMST`?
9. **`RiskCredit.public.nsccintradaycnsfailspositions` (phase 5).** How long is it retained, and which `slice_id` is the end-of-day position? The profiling SQL is in `sql/persistence/01_fail_charge_profile.sql`.

## 6. Decision log for this request

| Date | Decision | By |
|---|---|---|
| 2026-10-14 | Request drafted. Extract procedures go in `OperationsPortalDB`; the three databases in group A use cross-database reads first, with wrapper entries as the fallback | Data & Decision Science |
| | Wrapper allowlist approved or declined | WSI |
| | Cross-database read for group A approved, or wrapper entries added | WSI |
| | Group B grants approved or declined | WSI |
| | `RiskCredit` path chosen | WSI / WTS |
