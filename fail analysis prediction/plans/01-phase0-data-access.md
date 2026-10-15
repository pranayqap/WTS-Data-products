# 01 — Phase 0a: Data Access Request and Extract Specification

- **Status:** Done 2026-10-14 (request and extract SQL drafted; WSI review and grants outstanding, tracked as Q1 and Q4 in the documentation)
- **Todo:** `phase0-access`
- **Depends on:** [00-master-plan.md](00-master-plan.md)

## Objective

Get bulk, read-only, point-in-time access to the WSI history that the labels and features need, without crossing the WSI/WTS entity boundary that the WSI DB Wrapper exists to protect.

## Inputs

- WSI DB Wrapper contract: `../../WTS Ops Mirror Repo/wsi-wts-database-wrapper/docs/API_Contract.md`, section 8.7 `POST /wsi-db-wrapper/stored-procs/execute`.
  - The allowlist lives in `OperationsPortalDB.dbo.WSIDBWrapperSPList`.
  - Limits: 100 calls a minute per caller, 300-second command timeout.
- Table definitions: `../../WTS Ops Mirror Repo/wsi-database-definitions/SQL Server/USDBLSTN/`.
- The source inventory in the documentation, section "Data sources".

## Steps

1. **Inventory every source by family:**
   - labels
   - trade universe
   - security and liquidity
   - inventory and borrow
   - fail pressure
   - calendar

   For each source, record the database, table, as-of key, required columns and whether it keeps history.
2. **Set the extract window.**
   - Labels run from 2024-05-28 (the T+1 cutover) to the present.
   - Features need a 250-business-day look-back, so every extract starts on 2023-05-01.
3. **Write one extract procedure per source family.** The rules for each procedure:
   - Name: `OperationsPortalDB.dbo.usp_FailModel_Extract_<Source>`. It lives in OperationsPortalDB because the existing fail-analysis procedures already read across databases from there.
   - Parameters: `@FromDate DATE`, `@ToDate DATE`, and `@RowCount INT OUTPUT`.
   - Reads only archived or dated tables, never current-state lookups.
   - Projects only the listed columns. No PII: no `SSNTIN`, no customer names.
   - Uses deterministic `ORDER BY` on the as-of key.
4. **Size the backfill against the wrapper limits.**
   - One call per source per business day keeps each call well inside the 300-second timeout.
   - Roughly 870 business days × 16 procedures ≈ 14,000 calls. At the wrapper's limit, that is about 2.5 hours of pacing.
   - The daily incremental is 16 calls.
   - If WSI prefers it, use a read-only replica login for the backfill and keep the wrapper for the daily incremental.
5. **Request access to the unmirrored databases:**
   - `LocateSecurity`: `EasyToBorrowListVersioned`, `ETBAnalysis`.
   - `Intranet`: `BorrowSecurities`, `BorrowStatus`.
   - `ReplicateDB_Archive`: `FAIMST_AUDIT_REPL`, `EODSTKREC_REPL`, `EODNMADDA_REPL`, `PHASE3_TRDDTL_REPL`.
   - Read-only `msdb` job history, to verify job timing.
6. **Agree on data handling.**
   - Extracts land under `Model/data/raw/<source>/` as Parquet.
   - That folder is git-ignored and sits on an encrypted disk.
   - Account numbers are kept, because they are needed for joins and counterparty history. Names are not extracted.

## Outputs

- [`../requests/wsi-data-access-request.md`](../requests/wsi-data-access-request.md): the request to send to WSI.
- [`../sql/extract/`](../sql/extract/): proposed T-SQL for each extract procedure, ready for WSI review.

## Exit criteria

- WSI has acknowledged the request, and the access path (wrapper allowlist or replica) is decided and logged.
- Each extract procedure is reviewed by WSI. Column names match the mirrored definitions.
- Grants for the unmirrored databases are approved or explicitly declined. A decline is logged as a limitation.

## Documentation sections updated

Data sources, Open questions, Decisions log, Changelog.
