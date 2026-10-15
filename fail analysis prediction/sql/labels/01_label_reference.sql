/*
    Phase 1 -- server-side reference implementation of the new-fail label (plan 03). Read-only, no PII.
    Returns daily counts per settle date, book and direction: in-population trades, positives (exact and
    Beta fallback matches), and trades whose label snapshot is missing (unknown, never negative).
    Compare with failmodel.labels.label_daily_counts (CLI: failmodel labels, file label_daily_counts.csv).

    Rules (documentation sections 3 and 4):
      S = contractual settle date; D = business day before S (ControlDB.dbo.DateMaster).
      Beta: Cancelled = 0, AccountType <> 8, SecurityType <> 'P', TradeQuantity <> 0, entered on or before D
            (entry date = first 10 characters of TradeTimestamp). Live Trade wins over BetaAccessArchive.
      P3:   latest SEQ_NBR per 7-part key, then blank CXL_COR_REASON, UNMATCHED_CANCEL <> 'Y',
            ACCT_TYPE NOT IN ('2','3'), SEC_TYPE NOT IN ('5','6'), QUANTITY <> 0, ENTRY_DATE on or before D.
      Label snapshot = S shifted by @BetaSnapshotOffsetBd business days for Beta (P3: S).
*/
DECLARE @FromDate DATE = '2024-05-28';
DECLARE @ToDate   DATE = DATEADD(DAY, -1, CAST(GETDATE() AS DATE));
DECLARE @BetaSnapshotOffsetBd INT = 0;
DECLARE @TradeFrom DATE = DATEADD(DAY, -45, @FromDate);

IF OBJECT_ID('tempdb..#bd') IS NOT NULL DROP TABLE #bd;
SELECT BusinessDate, ROW_NUMBER() OVER (ORDER BY BusinessDate) AS BdIndex
INTO #bd
FROM (SELECT DISTINCT CAST(ActualDate AS DATE) AS BusinessDate FROM ControlDB.dbo.DateMaster) AS d;

IF OBJECT_ID('tempdb..#snap') IS NOT NULL DROP TABLE #snap;
SELECT DISTINCT 'B' AS Book, CAST(SnapShotDate AS DATE) AS SnapDate
INTO #snap
FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo
WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 10, @ToDate);
INSERT #snap
SELECT DISTINCT 'P', CAST(SnapShotDate AS DATE)
FROM Phase3DB.dbo.EODFAIMST
WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 10, @ToDate);

/* Beta trade universe */
IF OBJECT_ID('tempdb..#beta') IS NOT NULL DROP TABLE #beta;
WITH src AS (
    SELECT ID, TradeDate, SettleDate, TradeTimestamp, ControlNumber, AccountNumber, AccountType, SecurityNumber,
           SecurityType, TradeQuantity, PurchaseOrSale, Cancelled, 1 AS SourcePriority
    FROM BetaAccess.dbo.Trade
    WHERE TradeDate >= @TradeFrom AND TradeDate < DATEADD(DAY, 1, @ToDate)
    UNION ALL
    SELECT ID, TradeDate, SettleDate, TradeTimestamp, ControlNumber, AccountNumber, AccountType, SecurityNumber,
           SecurityType, TradeQuantity, PurchaseOrSale, Cancelled, 2
    FROM BetaAccessArchive.dbo.Trade
    WHERE TradeDate >= @TradeFrom AND TradeDate < DATEADD(DAY, 1, @ToDate)
),
ranked AS (
    SELECT src.*, ROW_NUMBER() OVER (PARTITION BY ID, TradeDate ORDER BY SourcePriority) AS rn FROM src
)
SELECT t.ControlNumber, CAST(t.TradeDate AS DATE) AS TradeDate, t.AccountNumber, t.SecurityNumber,
       ABS(t.TradeQuantity) AS Qty, CASE t.PurchaseOrSale WHEN 'P' THEN 0 ELSE 1 END AS RD,
       CASE t.PurchaseOrSale WHEN 'P' THEN 'D' ELSE 'R' END AS Direction,
       s.BusinessDate AS SettleDate, lbl.BusinessDate AS LabelSnapshot
INTO #beta
FROM ranked AS t
JOIN #bd AS s ON s.BusinessDate = CAST(t.SettleDate AS DATE)
JOIN #bd AS d ON d.BdIndex = s.BdIndex - 1
JOIN #bd AS lbl ON lbl.BdIndex = s.BdIndex + @BetaSnapshotOffsetBd
WHERE t.rn = 1
  AND s.BusinessDate >= @FromDate AND s.BusinessDate <= @ToDate
  AND t.Cancelled = 0 AND t.AccountType <> 8 AND COALESCE(t.SecurityType, '') <> 'P'
  AND t.TradeQuantity <> 0 AND t.PurchaseOrSale IN ('P', 'S')
  AND COALESCE(TRY_CONVERT(DATE, LEFT(t.TradeTimestamp, 10)), CAST(t.TradeDate AS DATE)) <= d.BusinessDate;

/* P3 trade universe */
IF OBJECT_ID('tempdb..#p3') IS NOT NULL DROP TABLE #p3;
WITH src AS (
    SELECT TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, MARKET, TRAN_NBR, FIRM_NBR, SEQ_NBR, SIDE_CODE,
           SIDE_SEQ_NBR, SETTLE_FULL_DATE, ENTRY_DATE, BUY_SELL, SEC_TYPE, QUANTITY, CXL_COR_REASON, UNMATCHED_CANCEL,
           1 AS SourcePriority
    FROM ReplicateDB.dbo.TRDDTL_REPL
    WHERE TRADE_FULL_DATE >= @TradeFrom AND TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
    UNION ALL
    SELECT TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, MARKET, TRAN_NBR, FIRM_NBR, SEQ_NBR, SIDE_CODE,
           SIDE_SEQ_NBR, SETTLE_FULL_DATE, ENTRY_DATE, BUY_SELL, SEC_TYPE, QUANTITY, CXL_COR_REASON, UNMATCHED_CANCEL, 2
    FROM Phase3DB.dbo.TRDDTL_ARCH
    WHERE TRADE_FULL_DATE >= @TradeFrom AND TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
),
latest AS (
    SELECT src.*,
           ROW_NUMBER() OVER (PARTITION BY TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, MARKET, TRAN_NBR, FIRM_NBR
                              ORDER BY SEQ_NBR DESC, SIDE_CODE, SIDE_SEQ_NBR, SourcePriority) AS rn
    FROM src
)
SELECT CAST(t.TRADE_FULL_DATE AS DATE) AS TradeDate, t.ACCT_NBR, t.CCURRENCY, t.ACCT_TYPE, t.MARKET, t.TRAN_NBR,
       t.FIRM_NBR, CASE t.BUY_SELL WHEN 'B' THEN 'D' ELSE 'R' END AS Direction, s.BusinessDate AS SettleDate
INTO #p3
FROM latest AS t
JOIN #bd AS s ON s.BusinessDate = CAST(t.SETTLE_FULL_DATE AS DATE)
JOIN #bd AS d ON d.BdIndex = s.BdIndex - 1
WHERE t.rn = 1
  AND s.BusinessDate >= @FromDate AND s.BusinessDate <= @ToDate
  AND LTRIM(RTRIM(COALESCE(t.CXL_COR_REASON, ''))) = '' AND COALESCE(t.UNMATCHED_CANCEL, '') <> 'Y'
  AND t.ACCT_TYPE NOT IN ('2', '3') AND COALESCE(t.SEC_TYPE, '') NOT IN ('5', '6')
  AND t.QUANTITY <> 0 AND t.BUY_SELL IN ('B', 'S')
  AND COALESCE(CAST(t.ENTRY_DATE AS DATE), CAST(t.TRADE_FULL_DATE AS DATE)) <= d.BusinessDate;

/* Labels and daily counts */
WITH beta_labels AS (
    SELECT b.SettleDate, b.Direction,
           CASE WHEN EXISTS (SELECT 1 FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo AS f
                             WHERE CAST(f.SnapShotDate AS DATE) = b.LabelSnapshot AND f.ControlNumber = b.ControlNumber
                               AND f.ControlNumber <> 0 AND CAST(f.TradeDate AS DATE) = b.TradeDate
                               AND f.AccountNumber = b.AccountNumber AND f.SecurityNumber = b.SecurityNumber)
                THEN 'exact'
                WHEN EXISTS (SELECT 1 FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo AS f
                             WHERE CAST(f.SnapShotDate AS DATE) = b.LabelSnapshot AND f.ControlNumber = 0
                               AND CAST(f.TradeDate AS DATE) = b.TradeDate AND f.AccountNumber = b.AccountNumber
                               AND f.SecurityNumber = b.SecurityNumber AND f.ReceiveDeliverCode = b.RD
                               AND ABS(f.Quantity) = b.Qty)
                THEN 'fallback' END AS MatchMethod,
           CASE WHEN EXISTS (SELECT 1 FROM #snap WHERE Book = 'B' AND SnapDate = b.LabelSnapshot) THEN 1 ELSE 0 END AS SnapshotExists
    FROM #beta AS b
),
p3_labels AS (
    SELECT p.SettleDate, p.Direction,
           CASE WHEN EXISTS (SELECT 1 FROM Phase3DB.dbo.EODFAIMST AS e
                             WHERE CAST(e.SnapShotDate AS DATE) = p.SettleDate
                               AND CAST(e.TRADE_FULL_DATE AS DATE) = p.TradeDate AND e.ACCT_NBR = p.ACCT_NBR
                               AND e.CCURRENCY = p.CCURRENCY AND e.ACCT_TYPE = p.ACCT_TYPE AND e.MARKET = p.MARKET
                               AND e.TRAN_NBR = p.TRAN_NBR AND e.FIRM_NBR = p.FIRM_NBR)
                THEN 'exact' END AS MatchMethod,
           CASE WHEN EXISTS (SELECT 1 FROM #snap WHERE Book = 'P' AND SnapDate = p.SettleDate) THEN 1 ELSE 0 END AS SnapshotExists
    FROM #p3 AS p
),
all_labels AS (
    SELECT 'B' AS Book, * FROM beta_labels
    UNION ALL
    SELECT 'P', * FROM p3_labels
)
SELECT SettleDate, Book, Direction,
       COUNT_BIG(*) AS Trades,
       SUM(CASE WHEN MatchMethod IS NOT NULL THEN 1 ELSE 0 END) AS Positives,
       SUM(CASE WHEN MatchMethod IS NULL AND SnapshotExists = 0 THEN 1 ELSE 0 END) AS Unknown,
       SUM(CASE WHEN MatchMethod = 'exact' THEN 1 ELSE 0 END) AS ExactPositives,
       SUM(CASE WHEN MatchMethod = 'fallback' THEN 1 ELSE 0 END) AS FallbackPositives
FROM all_labels
GROUP BY SettleDate, Book, Direction
ORDER BY SettleDate, Book, Direction;
