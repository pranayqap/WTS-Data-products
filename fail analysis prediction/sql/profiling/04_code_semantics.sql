/*
    P6 -- code semantics. Read-only, no PII.
    1. P3: BUY_SELL vs sign of QUANTITY on fail rows (report: QUANTITY < 0 = Receive, > 0 = Deliver).
    2. Beta: PurchaseOrSale of the matched trade vs fail ReceiveDeliverCode (expected P -> 0, S -> 1).
    3. Beta: share of fail rows with ControlNumber = 0.
    4. CNS S1: Cycle values and cycles per date (sets cns_eod_cycle).
    5. DVP: trade-level codes vs the current-state account flags the report uses, on recent trades only
       (current state is only trustworthy for the last few weeks).
    6. Format samples for TradeTimestamp and ExecutionTime (entry-date and execution-hour parsing).
*/
DECLARE @FromDate DATE = '2024-05-28';
DECLARE @ToDate   DATE = CAST(GETDATE() AS DATE);
DECLARE @RecentFrom DATE = DATEADD(DAY, -20, @ToDate);

/* 1 */
SELECT BUY_SELL, SIGN(QUANTITY) AS QuantitySign, SIGN(NET_AMOUNT) AS NetAmountSign, COUNT_BIG(*) AS FailRows
FROM Phase3DB.dbo.EODFAIMST
WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
GROUP BY BUY_SELL, SIGN(QUANTITY), SIGN(NET_AMOUNT)
ORDER BY FailRows DESC;

/* 2 */
SELECT t.PurchaseOrSale, f.ReceiveDeliverCode, SIGN(f.Quantity) AS FailQuantitySign, SIGN(t.TradeQuantity) AS TradeQuantitySign,
       COUNT_BIG(*) AS MatchedFailRows
FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo AS f
JOIN BetaAccessArchive.dbo.Trade AS t
  ON t.ControlNumber = f.ControlNumber AND t.TradeDate = f.TradeDate
 AND t.AccountNumber = f.AccountNumber AND t.SecurityNumber = f.SecurityNumber
WHERE f.SnapShotDate >= @FromDate AND f.SnapShotDate < DATEADD(DAY, 1, @ToDate) AND f.ControlNumber <> 0
GROUP BY t.PurchaseOrSale, f.ReceiveDeliverCode, SIGN(f.Quantity), SIGN(t.TradeQuantity)
ORDER BY MatchedFailRows DESC;

/* 3 */
SELECT FORMAT(SnapShotDate, 'yyyy-MM') AS SnapMonth, COUNT_BIG(*) AS FailRows,
       AVG(CASE WHEN ControlNumber = 0 THEN 1.0 ELSE 0.0 END) AS ZeroControlShare
FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo
WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
GROUP BY FORMAT(SnapShotDate, 'yyyy-MM')
ORDER BY SnapMonth;

/* 4 */
SELECT 'cns_p3' AS SourceId, Cycle, COUNT_BIG(*) AS RowsWithCycle, COUNT(DISTINCT CAST(SettleDate AS DATE)) AS DatesWithCycle,
       MIN(DATEPART(HOUR, UpDateDateTime)) AS MinLoadHour, MAX(DATEPART(HOUR, UpDateDateTime)) AS MaxLoadHour
FROM Phase3DB.dbo.CNS_8199_S1_Data
WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate)
GROUP BY Cycle
UNION ALL
SELECT 'cns_beta', Cycle, COUNT_BIG(*), COUNT(DISTINCT CAST(TradeDate AS DATE)),
       MIN(DATEPART(HOUR, UpDateDateTime)), MAX(DATEPART(HOUR, UpDateDateTime))
FROM NSCC.dbo.CNS_S1_Data
WHERE TradeDate >= @FromDate AND TradeDate < DATEADD(DAY, 1, @ToDate)
GROUP BY Cycle
ORDER BY SourceId, Cycle;

/* 5 */
SELECT 'beta' AS Book, t.StandingInstructionCode AS TradeCode,
       CONCAT(a.CashStdInst1, '/', a.CashStdInst2) AS AccountFlag,
       CASE WHEN a.CashStdInst1 = '5' AND a.CashStdInst2 = 'B' THEN 1 ELSE 0 END AS ReportDvp,
       COUNT_BIG(*) AS Trades
FROM BetaAccess.dbo.Trade AS t
LEFT JOIN BetaAccess.dbo.Account AS a ON a.AccountNumber = t.AccountNumber
WHERE t.TradeDate >= @RecentFrom
GROUP BY t.StandingInstructionCode, CONCAT(a.CashStdInst1, '/', a.CashStdInst2),
         CASE WHEN a.CashStdInst1 = '5' AND a.CashStdInst2 = 'B' THEN 1 ELSE 0 END
UNION ALL
SELECT 'p3', d.DEL_CODE, n.DEL, CASE WHEN n.DEL = 'V' THEN 1 ELSE 0 END, COUNT_BIG(*)
FROM ReplicateDB.dbo.TRDDTL_REPL AS d
LEFT JOIN ReplicateDB.dbo.NMARED_REPL AS n ON n.ACCT_NBR = d.ACCT_NBR
WHERE d.TRADE_FULL_DATE >= @RecentFrom
GROUP BY d.DEL_CODE, n.DEL, CASE WHEN n.DEL = 'V' THEN 1 ELSE 0 END
ORDER BY Book, Trades DESC;

/* 6 */
SELECT TOP 20 TradeTimestamp, ExecutionTime, CAST(TradeDate AS DATE) AS TradeDate, CAST(AsOfDate AS DATE) AS AsOfDate
FROM BetaAccess.dbo.Trade
WHERE TradeDate >= @RecentFrom
ORDER BY NEWID();
