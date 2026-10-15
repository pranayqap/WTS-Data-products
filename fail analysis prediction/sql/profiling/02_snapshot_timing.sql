/*
    P2, P3, P4 -- snapshot timing. Read-only, no PII.
    1. Load time of each snapshot relative to its SnapShotDate (hours after midnight of SnapShotDate).
       A D snapshot is usable at 22:30 ET on D only if it lands before 22:30 on D (hours < 22.5).
    2. Age-1 alignment: business days from SettleDate to the first snapshot showing each fail.
       The modal value sets beta_snapshot_offset_bd (0 = the snapshot dated S shows S's fails).
    3. StockLoanBorrow / BorrowForNeeds have no load timestamp; row counts per SettleDate are listed
       and the optional msdb section gives job run times if WSI grants msdb read.
*/
DECLARE @FromDate DATE = '2024-05-28';
DECLARE @ToDate   DATE = CAST(GETDATE() AS DATE);

/* 1. Landing time per snapshot date */
SELECT 'beta_fail_snapshots' AS SourceId, CAST(SnapShotDate AS DATE) AS SnapDate, DATENAME(WEEKDAY, SnapShotDate) AS Weekday,
       COUNT_BIG(*) AS RowsOnDate,
       DATEDIFF(MINUTE, CAST(CAST(SnapShotDate AS DATE) AS DATETIME), MIN(UpdateDateTime)) / 60.0 AS FirstLoadHours,
       DATEDIFF(MINUTE, CAST(CAST(SnapShotDate AS DATE) AS DATETIME), MAX(UpdateDateTime)) / 60.0 AS LastLoadHours
FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo
WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(SnapShotDate AS DATE), DATENAME(WEEKDAY, SnapShotDate)
UNION ALL
SELECT 'beta_security', CAST(SnapShotDate AS DATE), DATENAME(WEEKDAY, SnapShotDate), COUNT_BIG(*),
       DATEDIFF(MINUTE, CAST(CAST(SnapShotDate AS DATE) AS DATETIME), MIN(UpdateDateTime)) / 60.0,
       DATEDIFF(MINUTE, CAST(CAST(SnapShotDate AS DATE) AS DATETIME), MAX(UpdateDateTime)) / 60.0
FROM BetaDailyArchive.dbo.DailyArch_Security
WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(SnapShotDate AS DATE), DATENAME(WEEKDAY, SnapShotDate)
UNION ALL
SELECT 'beta_segregation', CAST(SnapShotDate AS DATE), DATENAME(WEEKDAY, SnapShotDate), COUNT_BIG(*),
       DATEDIFF(MINUTE, CAST(CAST(SnapShotDate AS DATE) AS DATETIME), MIN(UpdateDateTime)) / 60.0,
       DATEDIFF(MINUTE, CAST(CAST(SnapShotDate AS DATE) AS DATETIME), MAX(UpdateDateTime)) / 60.0
FROM BetaDailyArchive.dbo.DailyArch_SegregationAnalysisInfo
WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(SnapShotDate AS DATE), DATENAME(WEEKDAY, SnapShotDate)
UNION ALL
SELECT 'cns_p3', CAST(SettleDate AS DATE), DATENAME(WEEKDAY, SettleDate), COUNT_BIG(*),
       DATEDIFF(MINUTE, CAST(CAST(SettleDate AS DATE) AS DATETIME), MIN(UpDateDateTime)) / 60.0,
       DATEDIFF(MINUTE, CAST(CAST(SettleDate AS DATE) AS DATETIME), MAX(UpDateDateTime)) / 60.0
FROM Phase3DB.dbo.CNS_8199_S1_Data
WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(SettleDate AS DATE), DATENAME(WEEKDAY, SettleDate)
UNION ALL
SELECT 'smartloan_avail', CAST(FileDate AS DATE), DATENAME(WEEKDAY, FileDate), COUNT_BIG(*),
       DATEDIFF(MINUTE, CAST(CAST(FileDate AS DATE) AS DATETIME), MIN(LastUpdateDateTime)) / 60.0,
       DATEDIFF(MINUTE, CAST(CAST(FileDate AS DATE) AS DATETIME), MAX(LastUpdateDateTime)) / 60.0
FROM ReportDB.dbo.SmartLoanSLAvail
WHERE FileDate >= @FromDate AND FileDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(FileDate AS DATE), DATENAME(WEEKDAY, FileDate)
UNION ALL
SELECT 'p3_rpe_fails', CAST(SnapshotDate AS DATE), DATENAME(WEEKDAY, SnapshotDate), COUNT_BIG(*),
       DATEDIFF(MINUTE, CAST(CAST(SnapshotDate AS DATE) AS DATETIME), MIN(UpdateDateTime)) / 60.0,
       DATEDIFF(MINUTE, CAST(CAST(SnapshotDate AS DATE) AS DATETIME), MAX(UpdateDateTime)) / 60.0
FROM Phase3DB.dbo.vw_RPE200_RPE201_Combined
WHERE SnapshotDate >= @FromDate AND SnapshotDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(SnapshotDate AS DATE), DATENAME(WEEKDAY, SnapshotDate)
ORDER BY SourceId, SnapDate;

/* 2. Age-1 alignment: business days from SettleDate to the first snapshot showing the fail */
WITH bd AS (
    SELECT CAST(ActualDate AS DATE) AS BusinessDate, ROW_NUMBER() OVER (ORDER BY ActualDate) AS BdIndex
    FROM (SELECT DISTINCT CAST(ActualDate AS DATE) AS ActualDate FROM ControlDB.dbo.DateMaster) AS d
),
beta_first AS (
    SELECT CASE WHEN ControlNumber = 0
                THEN CONCAT('Z|', CONVERT(CHAR(8), SettleDate, 112), '|', AccountNumber, '|', ReceiveDeliverCode, '|', SecurityNumber)
                ELSE CONCAT('F|', FailUniqueId) END AS FailKey,
           MIN(CAST(SettleDate AS DATE)) AS SettleDate,
           MIN(CAST(SnapShotDate AS DATE)) AS FirstSnapshot
    FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND SettleDate >= DATEADD(DAY, 7, @FromDate) AND AccountType <> 8 AND SecurityTypeCode <> 'P'
    GROUP BY CASE WHEN ControlNumber = 0
                  THEN CONCAT('Z|', CONVERT(CHAR(8), SettleDate, 112), '|', AccountNumber, '|', ReceiveDeliverCode, '|', SecurityNumber)
                  ELSE CONCAT('F|', FailUniqueId) END
),
p3_first AS (
    SELECT CONCAT(CONVERT(CHAR(8), TRADE_FULL_DATE, 112), '|', ACCT_NBR, '|', CCURRENCY, '|', ACCT_TYPE, '|', MARKET, '|', TRAN_NBR, '|', FIRM_NBR) AS FailKey,
           MIN(CAST(SETTLE_DATE AS DATE)) AS SettleDate,
           MIN(CAST(SnapShotDate AS DATE)) AS FirstSnapshot
    FROM Phase3DB.dbo.EODFAIMST
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND SETTLE_DATE >= DATEADD(DAY, 7, @FromDate) AND ACCT_TYPE NOT IN ('2', '3')
    GROUP BY CONCAT(CONVERT(CHAR(8), TRADE_FULL_DATE, 112), '|', ACCT_NBR, '|', CCURRENCY, '|', ACCT_TYPE, '|', MARKET, '|', TRAN_NBR, '|', FIRM_NBR)
),
aligned AS (
    SELECT 'B' AS Book, f.FailKey, s.BdIndex AS SettleIdx, p.BdIndex AS SnapIdx FROM beta_first AS f
    CROSS APPLY (SELECT TOP 1 BdIndex FROM bd WHERE bd.BusinessDate <= f.SettleDate ORDER BY bd.BusinessDate DESC) AS s
    CROSS APPLY (SELECT TOP 1 BdIndex FROM bd WHERE bd.BusinessDate <= f.FirstSnapshot ORDER BY bd.BusinessDate DESC) AS p
    UNION ALL
    SELECT 'P', f.FailKey, s.BdIndex, p.BdIndex FROM p3_first AS f
    CROSS APPLY (SELECT TOP 1 BdIndex FROM bd WHERE bd.BusinessDate <= f.SettleDate ORDER BY bd.BusinessDate DESC) AS s
    CROSS APPLY (SELECT TOP 1 BdIndex FROM bd WHERE bd.BusinessDate <= f.FirstSnapshot ORDER BY bd.BusinessDate DESC) AS p
)
SELECT Book, SnapIdx - SettleIdx AS FirstSnapshotMinusSettleBd, COUNT_BIG(*) AS Fails
FROM aligned
GROUP BY Book, SnapIdx - SettleIdx
ORDER BY Book, FirstSnapshotMinusSettleBd;

/* 3. StockLoanBorrow / BorrowForNeeds: rows per SettleDate (no load timestamp exists) */
SELECT 'stock_loan_borrow' AS SourceId, CAST(SettleDate AS DATE) AS SettleDate, COUNT_BIG(*) AS RowsOnDate,
       SUM(CASE WHEN OpenReceiveDeliver <> 0 THEN 1 ELSE 0 END) AS RowsWithOpenReceiveDeliver
FROM NSCC.dbo.StockLoanBorrow
WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(SettleDate AS DATE)
UNION ALL
SELECT 'borrow_for_needs', CAST(SettleDate AS DATE), COUNT_BIG(*), SUM(CASE WHEN OpenFailToDeliver <> 0 THEN 1 ELSE 0 END)
FROM NSCC.dbo.BorrowForNeeds
WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate)
GROUP BY CAST(SettleDate AS DATE)
ORDER BY SourceId, SettleDate;

/* Optional (needs msdb read): SQL Agent run times for the loads above.
SELECT j.name AS JobName, s.step_name, h.run_date, h.run_time, h.run_duration, h.run_status
FROM msdb.dbo.sysjobs AS j
JOIN msdb.dbo.sysjobhistory AS h ON h.job_id = j.job_id
LEFT JOIN msdb.dbo.sysjobsteps AS s ON s.job_id = j.job_id AND s.step_id = h.step_id
WHERE (j.name LIKE '%StockLoanBorrow%' OR j.name LIKE '%BorrowForNeeds%' OR j.name LIKE '%DailyArch%' OR j.name LIKE '%fal%')
  AND h.run_date >= CONVERT(INT, CONVERT(CHAR(8), DATEADD(DAY, -30, GETDATE()), 112))
ORDER BY j.name, h.run_date, h.run_time;
*/
