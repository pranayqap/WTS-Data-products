/*
    Fail Prediction Model -- P3 extracts (host: OperationsPortalDB). Requires 00_common.sql.
    Sources: ReplicateDB, Phase3DB. Older P3 trade detail may only exist in ReplicateDB_Archive
    (PHASE3_TRDDTL_REPL), which is not mirrored; profiling check P1 measures coverage.
*/

/* Trade universe. ReplicateDB.TRDDTL_REPL and Phase3DB.TRDDTL_ARCH share the SB_TRDDTL unique key;
   the replicated row wins on overlap. All sides and sequence numbers are returned; the Python
   canonical layer keeps the latest SEQ_NBR per trade key (rule confirmed by profiling check P5). */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_P3Trades
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    WITH src AS (
        SELECT r.FIRM_NBR, r.ACCT_NBR, r.CCURRENCY, r.ACCT_TYPE, r.TRADE_FULL_DATE, r.SETTLE_FULL_DATE,
               r.SEQ_NBR, r.MARKET, r.TRAN_NBR, r.SIDE_CODE, r.SIDE_SEQ_NBR, r.BUY_SELL,
               r.CORRECTION_CODE, r.CXL_COR_REASON, r.UNMATCHED_CANCEL, r.CAPACITY_CODE, r.CUST_TYPE,
               r.ID_TRADE_IND, r.CUSIP_NUMBER, r.SEC_TYPE, r.SEC_SUB_TYPE, r.QUANTITY, r.PRICE,
               r.NET_AMOUNT, r.CONTRA_ACCT_NBR_1, r.CONTRA_ACCT_TYPE_1, r.DEL_CODE, r.PAY_CODE,
               r.SPECIAL_PAY_DEL, r.EXEC_HH, r.EXEC_MM, r.ENTRY_DATE, r.ENTRY_TIME,
               r.SETTLE_DATE_STATUS, r.TBA_FAIL_REASON, 1 AS SourcePriority
        FROM ReplicateDB.dbo.TRDDTL_REPL AS r
        WHERE r.TRADE_FULL_DATE >= @FromDate AND r.TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
        UNION ALL
        SELECT a.FIRM_NBR, a.ACCT_NBR, a.CCURRENCY, a.ACCT_TYPE, a.TRADE_FULL_DATE, a.SETTLE_FULL_DATE,
               a.SEQ_NBR, a.MARKET, a.TRAN_NBR, a.SIDE_CODE, a.SIDE_SEQ_NBR, a.BUY_SELL,
               a.CORRECTION_CODE, a.CXL_COR_REASON, a.UNMATCHED_CANCEL, a.CAPACITY_CODE, a.CUST_TYPE,
               a.ID_TRADE_IND, a.CUSIP_NUMBER, a.SEC_TYPE, a.SEC_SUB_TYPE, a.QUANTITY, a.PRICE,
               a.NET_AMOUNT, a.CONTRA_ACCT_NBR_1, a.CONTRA_ACCT_TYPE_1, a.DEL_CODE, a.PAY_CODE,
               a.SPECIAL_PAY_DEL, a.EXEC_HH, a.EXEC_MM, a.ENTRY_DATE, a.ENTRY_TIME,
               a.SETTLE_DATE_STATUS, a.TBA_FAIL_REASON, 2 AS SourcePriority
        FROM Phase3DB.dbo.TRDDTL_ARCH AS a
        WHERE a.TRADE_FULL_DATE >= @FromDate AND a.TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
    ),
    ranked AS (
        SELECT src.*,
               ROW_NUMBER() OVER (
                   PARTITION BY TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, TRAN_NBR, SEQ_NBR,
                                SETTLE_FULL_DATE, MARKET, SIDE_CODE, SIDE_SEQ_NBR, FIRM_NBR
                   ORDER BY SourcePriority) AS rn
        FROM src
    )
    SELECT FIRM_NBR, ACCT_NBR, CCURRENCY, ACCT_TYPE, TRADE_FULL_DATE, SETTLE_FULL_DATE, SEQ_NBR, MARKET,
           TRAN_NBR, SIDE_CODE, SIDE_SEQ_NBR, BUY_SELL, CORRECTION_CODE, CXL_COR_REASON,
           UNMATCHED_CANCEL, CAPACITY_CODE, CUST_TYPE, ID_TRADE_IND, CUSIP_NUMBER, SEC_TYPE,
           SEC_SUB_TYPE, QUANTITY, PRICE, NET_AMOUNT, CONTRA_ACCT_NBR_1, CONTRA_ACCT_TYPE_1, DEL_CODE,
           PAY_CODE, SPECIAL_PAY_DEL, EXEC_HH, EXEC_MM, ENTRY_DATE, ENTRY_TIME, SETTLE_DATE_STATUS,
           TBA_FAIL_REASON
    FROM ranked
    WHERE rn = 1
    ORDER BY TRADE_FULL_DATE, ACCT_NBR, TRAN_NBR, SEQ_NBR;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Label source and fail pressure. Read directly from EODFAIMST (not the RPE combined view), so labels
   are unaffected by the 2025-08-14 report source switch. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_P3FailSnapshots
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT e.ID, e.SnapShotDate, e.FIRM_NBR, e.CUSIP_NUMBER, e.RECORD_TYPE, e.ACCT_NBR, e.CCURRENCY,
           e.ACCT_TYPE, e.TRADE_FULL_DATE, e.MARKET, e.TRAN_NBR, e.QUANTITY, e.ORIGINAL_QUANTITY,
           e.NET_AMOUNT, e.SETTLE_DATE, e.ENTRY_DATE, e.BUY_SELL, e.FAIL_REASON, e.FAIL_PRODUCT,
           e.CONTRA_ACCT_NBR_1, e.DTC_NBR, e.DTC_DO_REASON_CODE, e.ID_TRD_MATCHING, e.ID_TRD_AFFIRMATION
    FROM Phase3DB.dbo.EODFAIMST AS e
    WHERE e.SnapShotDate >= @FromDate AND e.SnapShotDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY e.SnapShotDate, e.ID;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Report-side P3 fail population from 2025-08-14. GetP3FailAnalysisData_Report reads the RPE200/RPE201 files
   (vw_RPE_FILES_AND_EODFAIL_MASTER_COMBINED) from that date and EODFAIMST before it; the Summary procedure reads
   the RPE view for all dates. Used only to reconcile EODFAIMST-based labels (profiling check P8).
   AccountName and RepCode are deliberately excluded. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_P3RpeFails
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT 'RPE200' AS FileSource, r.SnapshotDate AS SnapShotDate, r.AccountNumber, r.AccountType, r.TradeDate,
           r.SettleDate, r.Quantity, r.CUSIP, r.NetAmount, r.NetAmountCurrency, r.MarketValue, r.SecType,
           r.RecType, r.PRODUCT, r.TranNbr, r.BuySell, r.Market, CAST(NULL AS CHAR(4)) AS DTCNumber,
           r.UpdateDateTime
    FROM Phase3DB.dbo.RPE200 AS r
    WHERE r.SnapshotDate >= @FromDate AND r.SnapshotDate < DATEADD(DAY, 1, @ToDate)
    UNION ALL
    SELECT 'RPE201', s.SnapShotDate, s.AccountNumber, s.AccountType, s.TradeDate, s.SettleDate, s.Quantity,
           s.CUSIP, s.NetAmount, s.NetAmountCurrency, s.MarketValue, CAST(NULL AS VARCHAR(1)),
           CAST(NULL AS VARCHAR(1)), CAST(NULL AS VARCHAR(4)), s.TranNbr, s.BuySell, s.Market, s.DTCNumber,
           s.UpdateDateTime
    FROM Phase3DB.dbo.RPE201 AS s
    WHERE s.SnapShotDate >= @FromDate AND s.SnapShotDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY SnapShotDate, AccountNumber, CUSIP;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* P3 end-of-day price by snapshot (point-in-time replacement for the current-state SECBAS/POLDAT joins). */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_P3EodPrices
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT p.SnapShotDate, p.CUSIP_NUMBER, p.CURR_PRICE, p.POOL_FACTOR, p.SECURITY_FACTOR, p.PRICE_FULL_DATE
    FROM ReplicateDB.dbo.EODCMVDAT_REPL AS p
    WHERE p.SnapShotDate >= @FromDate AND p.SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(p.CUSIP_NUMBER)), 9))
    ORDER BY p.SnapShotDate, p.CUSIP_NUMBER;

    SET @RowCount = @@ROWCOUNT;
END;
GO
