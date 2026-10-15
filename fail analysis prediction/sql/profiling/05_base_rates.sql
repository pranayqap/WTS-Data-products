/*
    P7, P8 -- base rates and the 2025-08-14 P3 source switch. Read-only, no PII.
    P7: settling in-population trades and Age-1 fails per settle date, book and direction.
        The ratio approximates the label base rate before trade-level matching.
    P8: GetP3FailAnalysisData_Report reads Phase3DB.dbo.EODFAIMST (vw_EODFAIL_MASTER_PHASE3_DB_ONLY) before
        2025-08-14 and the RPE200/RPE201 files (vw_RPE_FILES_AND_EODFAIL_MASTER_COMBINED) from that date; the
        Summary procedure reads the RPE view for every date. Labels read EODFAIMST throughout. This compares
        daily rows in EODFAIMST, the RPE files and ReplicateDB.dbo.EODFAIMST_REPL, then the share of Age-1 RPE
        rows that have an EODFAIMST row on the view's join key (and the reverse).
*/
DECLARE @FromDate DATE = '2024-05-28';
DECLARE @ToDate   DATE = CAST(GETDATE() AS DATE);
DECLARE @Switch   DATE = '2025-08-14';

/* P7 */
WITH beta_trades AS (
    SELECT CAST(SettleDate AS DATE) AS SettleDate, CASE PurchaseOrSale WHEN 'P' THEN 'D' WHEN 'S' THEN 'R' END AS Direction
    FROM BetaAccessArchive.dbo.Trade
    WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate)
      AND Cancelled = 0 AND AccountType <> 8 AND SecurityType <> 'P' AND TradeQuantity <> 0
    UNION ALL
    SELECT CAST(SettleDate AS DATE), CASE PurchaseOrSale WHEN 'P' THEN 'D' WHEN 'S' THEN 'R' END
    FROM BetaAccess.dbo.Trade
    WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate)
      AND Cancelled = 0 AND AccountType <> 8 AND SecurityType <> 'P' AND TradeQuantity <> 0
      AND NOT EXISTS (SELECT 1 FROM BetaAccessArchive.dbo.Trade AS a WHERE a.ID = BetaAccess.dbo.Trade.ID AND a.TradeDate = BetaAccess.dbo.Trade.TradeDate)
),
beta_fails AS (
    SELECT CAST(SnapShotDate AS DATE) AS SettleDate, CASE ReceiveDeliverCode WHEN 0 THEN 'D' WHEN 1 THEN 'R' END AS Direction
    FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND CAST(SettleDate AS DATE) = CAST(SnapShotDate AS DATE) AND AccountType <> 8 AND SecurityTypeCode <> 'P'
),
p3_trades AS (
    SELECT CAST(SETTLE_FULL_DATE AS DATE) AS SettleDate, CASE BUY_SELL WHEN 'B' THEN 'D' WHEN 'S' THEN 'R' END AS Direction
    FROM ReplicateDB.dbo.TRDDTL_REPL
    WHERE SETTLE_FULL_DATE >= @FromDate AND SETTLE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
      AND ACCT_TYPE NOT IN ('2', '3') AND COALESCE(SEC_TYPE, '') NOT IN ('5', '6') AND QUANTITY <> 0
      AND LTRIM(RTRIM(COALESCE(CXL_COR_REASON, ''))) = '' AND COALESCE(UNMATCHED_CANCEL, '') <> 'Y'
),
p3_fails AS (
    SELECT CAST(SnapShotDate AS DATE) AS SettleDate, CASE WHEN QUANTITY < 0 OR NET_AMOUNT < 0 THEN 'R' ELSE 'D' END AS Direction
    FROM Phase3DB.dbo.EODFAIMST
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND CAST(SETTLE_DATE AS DATE) = CAST(SnapShotDate AS DATE) AND ACCT_TYPE NOT IN ('2', '3')
),
counts AS (
    SELECT 'B' AS Book, SettleDate, Direction, COUNT_BIG(*) AS SettlingTrades, 0 AS Age1Fails FROM beta_trades GROUP BY SettleDate, Direction
    UNION ALL SELECT 'B', SettleDate, Direction, 0, COUNT_BIG(*) FROM beta_fails GROUP BY SettleDate, Direction
    UNION ALL SELECT 'P', SettleDate, Direction, COUNT_BIG(*), 0 FROM p3_trades GROUP BY SettleDate, Direction
    UNION ALL SELECT 'P', SettleDate, Direction, 0, COUNT_BIG(*) FROM p3_fails GROUP BY SettleDate, Direction
)
SELECT Book, Direction, FORMAT(SettleDate, 'yyyy-MM') AS SettleMonth,
       SUM(SettlingTrades) AS SettlingTrades, SUM(Age1Fails) AS Age1Fails,
       CAST(SUM(Age1Fails) AS FLOAT) / NULLIF(SUM(SettlingTrades), 0) AS ApproxBaseRate,
       COUNT(DISTINCT SettleDate) AS SettleDates
FROM counts
GROUP BY Book, Direction, FORMAT(SettleDate, 'yyyy-MM')
ORDER BY Book, Direction, SettleMonth;

/* P8, part 1: daily rows per source, summarised before and after the switch */
WITH e AS (
    SELECT CAST(SnapShotDate AS DATE) AS SnapDate, COUNT_BIG(*) AS EodfaimstRows FROM Phase3DB.dbo.EODFAIMST
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE)
),
r AS (
    SELECT CAST(SnapshotDate AS DATE) AS SnapDate, COUNT_BIG(*) AS RpeRows
    FROM Phase3DB.dbo.vw_RPE200_RPE201_Combined
    WHERE SnapshotDate >= @FromDate AND SnapshotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapshotDate AS DATE)
),
p AS (
    SELECT CAST(SnapShotDate AS DATE) AS SnapDate, COUNT_BIG(*) AS ReplRows FROM ReplicateDB.dbo.EODFAIMST_REPL
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE)
),
daily AS (
    SELECT COALESCE(e.SnapDate, r.SnapDate, p.SnapDate) AS SnapDate, e.EodfaimstRows, r.RpeRows, p.ReplRows
    FROM e FULL OUTER JOIN r ON e.SnapDate = r.SnapDate
    FULL OUTER JOIN p ON COALESCE(e.SnapDate, r.SnapDate) = p.SnapDate
)
SELECT CASE WHEN SnapDate < @Switch THEN 'before' ELSE 'after' END AS Period,
       COUNT(*) AS SnapshotDates,
       SUM(CASE WHEN EodfaimstRows IS NOT NULL THEN 1 ELSE 0 END) AS DatesWithEodfaimst,
       SUM(CASE WHEN RpeRows IS NOT NULL THEN 1 ELSE 0 END) AS DatesWithRpe,
       AVG(CAST(EodfaimstRows AS FLOAT)) AS AvgEodfaimstRows, AVG(CAST(RpeRows AS FLOAT)) AS AvgRpeRows,
       AVG(CAST(ReplRows AS FLOAT)) AS AvgReplRows
FROM daily
GROUP BY CASE WHEN SnapDate < @Switch THEN 'before' ELSE 'after' END;

/* P8, part 2: Age-1 key coverage between the RPE files and EODFAIMST, on the view's join key */
WITH rpe AS (
    SELECT CAST(SnapshotDate AS DATE) AS SnapDate, LTRIM(RTRIM(AccountNumber)) AS Acct, TranNbr, TradeDate,
           SettleDate, Market, LEFT(LTRIM(RTRIM(CUSIP)), 9) AS Cusip9, FileSource
    FROM Phase3DB.dbo.vw_RPE200_RPE201_Combined
    WHERE SnapshotDate >= @Switch AND SnapshotDate < DATEADD(DAY, 1, @ToDate)
      AND CAST(SettleDate AS DATE) = CAST(SnapshotDate AS DATE) AND AccountType NOT IN (2, 3)
),
eod AS (
    SELECT CAST(SnapShotDate AS DATE) AS SnapDate, LTRIM(RTRIM(ACCT_NBR)) AS Acct, TRAN_NBR, TRADE_FULL_DATE,
           SETTLE_DATE, MARKET, LEFT(COALESCE(NULLIF(LTRIM(RTRIM(CUSIP_NUMBER)), ''), '000000000'), 9) AS Cusip9, RECORD_TYPE
    FROM Phase3DB.dbo.EODFAIMST
    WHERE SnapShotDate >= @Switch AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND CAST(SETTLE_DATE AS DATE) = CAST(SnapShotDate AS DATE) AND ACCT_TYPE NOT IN ('2', '3')
)
SELECT 'rpe_rows_with_eodfaimst_match' AS Measure, rpe.FileSource AS Detail, COUNT_BIG(*) AS Age1Rows,
       AVG(CASE WHEN EXISTS (SELECT 1 FROM eod WHERE eod.SnapDate = rpe.SnapDate AND eod.Acct = rpe.Acct
                             AND eod.TRAN_NBR = rpe.TranNbr AND eod.TRADE_FULL_DATE = rpe.TradeDate
                             AND eod.MARKET = rpe.Market AND eod.Cusip9 = rpe.Cusip9) THEN 1.0 ELSE 0.0 END) AS MatchShare
FROM rpe GROUP BY rpe.FileSource
UNION ALL
SELECT 'eodfaimst_rows_with_rpe_match', eod.RECORD_TYPE, COUNT_BIG(*),
       AVG(CASE WHEN EXISTS (SELECT 1 FROM rpe WHERE rpe.SnapDate = eod.SnapDate AND rpe.Acct = eod.Acct
                             AND rpe.TranNbr = eod.TRAN_NBR AND rpe.TradeDate = eod.TRADE_FULL_DATE
                             AND rpe.Market = eod.MARKET AND rpe.Cusip9 = eod.Cusip9) THEN 1.0 ELSE 0.0 END)
FROM eod GROUP BY eod.RECORD_TYPE;
