/*
    P5 -- trade-to-fail join hit rates and P3 trade rows per key. Read-only, no PII.
    Age-1 fails use the report filters (snapshot dated S, Beta AccountType <> 8 and SecurityTypeCode <> 'P',
    P3 ACCT_TYPE NOT IN ('2','3')). Beta matches exactly on ControlNumber + TradeDate + AccountNumber +
    SecurityNumber; rows with ControlNumber = 0 fall back to TradeDate + AccountNumber + SecurityNumber +
    direction + absolute quantity. P3 matches on the 7-part key.
*/
DECLARE @FromDate DATE = '2024-05-28';
DECLARE @ToDate   DATE = CAST(GETDATE() AS DATE);

/* Beta */
WITH trades AS (
    SELECT TradeDate, ControlNumber, AccountNumber, SecurityNumber, TradeQuantity,
           CASE PurchaseOrSale WHEN 'P' THEN 0 WHEN 'S' THEN 1 END AS ReceiveDeliverCode
    FROM BetaAccess.dbo.Trade WHERE TradeDate >= DATEADD(DAY, -10, @FromDate) AND TradeDate < DATEADD(DAY, 1, @ToDate)
    UNION
    SELECT TradeDate, ControlNumber, AccountNumber, SecurityNumber, TradeQuantity,
           CASE PurchaseOrSale WHEN 'P' THEN 0 WHEN 'S' THEN 1 END
    FROM BetaAccessArchive.dbo.Trade WHERE TradeDate >= DATEADD(DAY, -10, @FromDate) AND TradeDate < DATEADD(DAY, 1, @ToDate)
),
age1 AS (
    SELECT f.*
    FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo AS f
    WHERE f.SnapShotDate >= @FromDate AND f.SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND CAST(f.SettleDate AS DATE) = CAST(f.SnapShotDate AS DATE)
      AND f.AccountType <> 8 AND f.SecurityTypeCode <> 'P'
),
matched AS (
    SELECT a.SnapShotDate, a.ControlNumber,
           CASE WHEN a.ControlNumber <> 0 AND EXISTS (
                    SELECT 1 FROM trades AS t WHERE t.ControlNumber = a.ControlNumber AND t.TradeDate = a.TradeDate
                      AND t.AccountNumber = a.AccountNumber AND t.SecurityNumber = a.SecurityNumber) THEN 'exact'
                WHEN a.ControlNumber = 0 AND EXISTS (
                    SELECT 1 FROM trades AS t WHERE t.TradeDate = a.TradeDate AND t.AccountNumber = a.AccountNumber
                      AND t.SecurityNumber = a.SecurityNumber AND t.ReceiveDeliverCode = a.ReceiveDeliverCode
                      AND ABS(t.TradeQuantity) = ABS(a.Quantity)) THEN 'fallback'
                ELSE 'unmatched' END AS MatchMethod
    FROM age1 AS a
)
SELECT 'B' AS Book, FORMAT(SnapShotDate, 'yyyy-MM') AS SettleMonth, COUNT_BIG(*) AS Age1Fails,
       SUM(CASE WHEN MatchMethod = 'exact' THEN 1 ELSE 0 END) AS ExactMatches,
       SUM(CASE WHEN MatchMethod = 'fallback' THEN 1 ELSE 0 END) AS FallbackMatches,
       SUM(CASE WHEN MatchMethod = 'unmatched' AND ControlNumber <> 0 THEN 1 ELSE 0 END) AS UnmatchedWithControl,
       SUM(CASE WHEN MatchMethod = 'unmatched' AND ControlNumber = 0 THEN 1 ELSE 0 END) AS UnmatchedZeroControl,
       AVG(CASE WHEN ControlNumber = 0 THEN 1.0 ELSE 0.0 END) AS ZeroControlShare
FROM matched
GROUP BY FORMAT(SnapShotDate, 'yyyy-MM')
ORDER BY SettleMonth;

/* P3 */
WITH trades AS (
    SELECT DISTINCT TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, MARKET, TRAN_NBR, FIRM_NBR
    FROM ReplicateDB.dbo.TRDDTL_REPL WHERE TRADE_FULL_DATE >= DATEADD(DAY, -10, @FromDate) AND TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
    UNION
    SELECT DISTINCT TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, MARKET, TRAN_NBR, FIRM_NBR
    FROM Phase3DB.dbo.TRDDTL_ARCH WHERE TRADE_FULL_DATE >= DATEADD(DAY, -10, @FromDate) AND TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
),
age1 AS (
    SELECT e.*
    FROM Phase3DB.dbo.EODFAIMST AS e
    WHERE e.SnapShotDate >= @FromDate AND e.SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND CAST(e.SETTLE_DATE AS DATE) = CAST(e.SnapShotDate AS DATE)
      AND e.ACCT_TYPE NOT IN ('2', '3')
)
SELECT 'P' AS Book, FORMAT(a.SnapShotDate, 'yyyy-MM') AS SettleMonth, a.RECORD_TYPE, COUNT_BIG(*) AS Age1Fails,
       SUM(CASE WHEN t.TRAN_NBR IS NOT NULL THEN 1 ELSE 0 END) AS Matches
FROM age1 AS a
LEFT JOIN trades AS t
  ON t.TRADE_FULL_DATE = a.TRADE_FULL_DATE AND t.ACCT_NBR = a.ACCT_NBR AND t.CCURRENCY = a.CCURRENCY
 AND t.ACCT_TYPE = a.ACCT_TYPE AND t.MARKET = a.MARKET AND t.TRAN_NBR = a.TRAN_NBR AND t.FIRM_NBR = a.FIRM_NBR
GROUP BY FORMAT(a.SnapShotDate, 'yyyy-MM'), a.RECORD_TYPE
ORDER BY SettleMonth, a.RECORD_TYPE;

/* P3 trade rows per 7-part key (sets the deduplication rule) */
WITH rows_per_key AS (
    SELECT TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, MARKET, TRAN_NBR, FIRM_NBR,
           COUNT(*) AS RowsPerKey, COUNT(DISTINCT SEQ_NBR) AS SeqNumbers,
           COUNT(DISTINCT CONCAT(SIDE_CODE, '|', SIDE_SEQ_NBR)) AS Sides,
           COUNT(DISTINCT SETTLE_FULL_DATE) AS SettleDates,
           SUM(CASE WHEN LTRIM(RTRIM(COALESCE(CXL_COR_REASON, ''))) <> '' THEN 1 ELSE 0 END) AS CancelCorrectRows
    FROM ReplicateDB.dbo.TRDDTL_REPL
    WHERE TRADE_FULL_DATE >= @FromDate AND TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
    GROUP BY TRADE_FULL_DATE, ACCT_NBR, CCURRENCY, ACCT_TYPE, MARKET, TRAN_NBR, FIRM_NBR
)
SELECT RowsPerKey, SeqNumbers, Sides, SettleDates, CASE WHEN CancelCorrectRows > 0 THEN 1 ELSE 0 END AS HasCancelCorrect,
       COUNT_BIG(*) AS TradeKeys
FROM rows_per_key
GROUP BY RowsPerKey, SeqNumbers, Sides, SettleDates, CASE WHEN CancelCorrectRows > 0 THEN 1 ELSE 0 END
ORDER BY TradeKeys DESC;
