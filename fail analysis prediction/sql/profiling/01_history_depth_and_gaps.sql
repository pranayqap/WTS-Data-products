/*
    P1 -- history depth and gaps per source, against ControlDB.dbo.DateMaster business dates.
    Read-only: SELECTs against WSI tables plus one table variable. No PII. Run off-hours.
    Result set 1: one row per source (first/last date, dates with rows, missing business dates).
    Result set 2: every missing business date per source (a missing label snapshot = unknown label).
*/
DECLARE @FromDate DATE = '2023-05-01';
DECLARE @ToDate   DATE = CAST(GETDATE() AS DATE);

DECLARE @src TABLE (SourceId VARCHAR(40) NOT NULL, AsOfDate DATE NOT NULL, RowsOnDate BIGINT NOT NULL,
                    PRIMARY KEY (SourceId, AsOfDate));

INSERT @src SELECT 'beta_trades_live', CAST(TradeDate AS DATE), COUNT_BIG(*) FROM BetaAccess.dbo.Trade
    WHERE TradeDate >= @FromDate AND TradeDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(TradeDate AS DATE);
INSERT @src SELECT 'beta_trades_archive', CAST(TradeDate AS DATE), COUNT_BIG(*) FROM BetaAccessArchive.dbo.Trade
    WHERE TradeDate >= @FromDate AND TradeDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(TradeDate AS DATE);
INSERT @src SELECT 'beta_fail_snapshots', CAST(SnapShotDate AS DATE), COUNT_BIG(*) FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE);
INSERT @src SELECT 'beta_security', CAST(SnapShotDate AS DATE), COUNT_BIG(*) FROM BetaDailyArchive.dbo.DailyArch_Security
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE);
INSERT @src SELECT 'beta_segregation', CAST(SnapShotDate AS DATE), COUNT_BIG(*) FROM BetaDailyArchive.dbo.DailyArch_SegregationAnalysisInfo
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE);
INSERT @src SELECT 'beta_id_confirms', CAST(SnapshotDate AS DATE), COUNT_BIG(*) FROM BetaDailyArchive.dbo.DailyArch_IDConfirms
    WHERE SnapshotDate >= @FromDate AND SnapshotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapshotDate AS DATE);
INSERT @src SELECT 'beta_close_history', CAST(TradeDate AS DATE), COUNT_BIG(*) FROM BetaAccessArchive.dbo.SecurityCloseHistory
    WHERE TradeDate >= @FromDate AND TradeDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(TradeDate AS DATE);
INSERT @src SELECT 'p3_trades_repl', CAST(TRADE_FULL_DATE AS DATE), COUNT_BIG(*) FROM ReplicateDB.dbo.TRDDTL_REPL
    WHERE TRADE_FULL_DATE >= @FromDate AND TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(TRADE_FULL_DATE AS DATE);
INSERT @src SELECT 'p3_trades_arch', CAST(TRADE_FULL_DATE AS DATE), COUNT_BIG(*) FROM Phase3DB.dbo.TRDDTL_ARCH
    WHERE TRADE_FULL_DATE >= @FromDate AND TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(TRADE_FULL_DATE AS DATE);
INSERT @src SELECT 'p3_fail_snapshots', CAST(SnapShotDate AS DATE), COUNT_BIG(*) FROM Phase3DB.dbo.EODFAIMST
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE);
INSERT @src SELECT 'p3_fail_snapshots_repl', CAST(SnapShotDate AS DATE), COUNT_BIG(*) FROM ReplicateDB.dbo.EODFAIMST_REPL
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE);
INSERT @src SELECT 'p3_rpe_fails', CAST(SnapshotDate AS DATE), COUNT_BIG(*) FROM Phase3DB.dbo.vw_RPE200_RPE201_Combined
    WHERE SnapshotDate >= @FromDate AND SnapshotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapshotDate AS DATE);
INSERT @src SELECT 'p3_eod_prices', CAST(SnapShotDate AS DATE), COUNT_BIG(*) FROM ReplicateDB.dbo.EODCMVDAT_REPL
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE);
INSERT @src SELECT 'p3_stock_record', CAST(SnapShotDate AS DATE), COUNT_BIG(*) FROM ReplicateDB.dbo.EODSTKREC_REPL
    WHERE SnapShotDate >= @FromDate AND SnapShotDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SnapShotDate AS DATE);
INSERT @src SELECT 'cns_p3', CAST(SettleDate AS DATE), COUNT_BIG(*) FROM Phase3DB.dbo.CNS_8199_S1_Data
    WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SettleDate AS DATE);
INSERT @src SELECT 'cns_beta', CAST(TradeDate AS DATE), COUNT_BIG(*) FROM NSCC.dbo.CNS_S1_Data
    WHERE TradeDate >= @FromDate AND TradeDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(TradeDate AS DATE);
INSERT @src SELECT 'smartloan_avail', CAST(FileDate AS DATE), COUNT_BIG(*) FROM ReportDB.dbo.SmartLoanSLAvail
    WHERE FileDate >= @FromDate AND FileDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(FileDate AS DATE);
INSERT @src SELECT 'loanet_positions', CAST(TradeDate AS DATE), COUNT_BIG(*) FROM ReportDB.dbo.LoanetOpenPositions
    WHERE TradeDate >= @FromDate AND TradeDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(TradeDate AS DATE);
INSERT @src SELECT 'borrow_rates', CAST(FileDate AS DATE), COUNT_BIG(*) FROM ReportDB.dbo.StockBorrowInterestRate
    WHERE FileDate >= @FromDate AND FileDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(FileDate AS DATE);
INSERT @src SELECT 'reg204', CAST(ReportDate AS DATE), COUNT_BIG(*) FROM ReportDB.dbo.CNS204FailSettlementData
    WHERE ReportDate >= @FromDate AND ReportDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(ReportDate AS DATE);
INSERT @src SELECT 'stock_loan_borrow', CAST(SettleDate AS DATE), COUNT_BIG(*) FROM NSCC.dbo.StockLoanBorrow
    WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SettleDate AS DATE);
INSERT @src SELECT 'borrow_for_needs', CAST(SettleDate AS DATE), COUNT_BIG(*) FROM NSCC.dbo.BorrowForNeeds
    WHERE SettleDate >= @FromDate AND SettleDate < DATEADD(DAY, 1, @ToDate) GROUP BY CAST(SettleDate AS DATE);

WITH bd AS (
    SELECT DISTINCT CAST(ActualDate AS DATE) AS BusinessDate
    FROM ControlDB.dbo.DateMaster
    WHERE ActualDate >= @FromDate AND ActualDate < DATEADD(DAY, 1, @ToDate)
),
rng AS (
    SELECT SourceId, MIN(AsOfDate) AS FirstDate, MAX(AsOfDate) AS LastDate,
           COUNT(*) AS DatesWithRows, SUM(RowsOnDate) AS TotalRows,
           AVG(CAST(RowsOnDate AS FLOAT)) AS AvgRowsPerDate
    FROM @src GROUP BY SourceId
),
expected AS (
    SELECT r.SourceId, b.BusinessDate
    FROM rng AS r JOIN bd AS b ON b.BusinessDate BETWEEN r.FirstDate AND r.LastDate
),
missing AS (
    SELECT e.SourceId, e.BusinessDate
    FROM expected AS e
    WHERE NOT EXISTS (SELECT 1 FROM @src AS s WHERE s.SourceId = e.SourceId AND s.AsOfDate = e.BusinessDate)
),
non_business AS (
    SELECT s.SourceId, COUNT(*) AS NonBusinessDatesWithRows
    FROM @src AS s WHERE NOT EXISTS (SELECT 1 FROM bd WHERE bd.BusinessDate = s.AsOfDate)
    GROUP BY s.SourceId
)
SELECT r.SourceId, r.FirstDate, r.LastDate, r.DatesWithRows, r.TotalRows, r.AvgRowsPerDate,
       (SELECT COUNT(*) FROM expected AS e WHERE e.SourceId = r.SourceId) AS BusinessDatesInRange,
       (SELECT COUNT(*) FROM missing AS m WHERE m.SourceId = r.SourceId) AS MissingBusinessDates,
       COALESCE(n.NonBusinessDatesWithRows, 0) AS NonBusinessDatesWithRows
FROM rng AS r
LEFT JOIN non_business AS n ON n.SourceId = r.SourceId
ORDER BY r.SourceId;

WITH bd AS (
    SELECT DISTINCT CAST(ActualDate AS DATE) AS BusinessDate
    FROM ControlDB.dbo.DateMaster
    WHERE ActualDate >= @FromDate AND ActualDate < DATEADD(DAY, 1, @ToDate)
),
rng AS (SELECT SourceId, MIN(AsOfDate) AS FirstDate, MAX(AsOfDate) AS LastDate FROM @src GROUP BY SourceId)
SELECT r.SourceId, b.BusinessDate AS MissingDate
FROM rng AS r
JOIN bd AS b ON b.BusinessDate BETWEEN r.FirstDate AND r.LastDate
WHERE NOT EXISTS (SELECT 1 FROM @src AS s WHERE s.SourceId = r.SourceId AND s.AsOfDate = b.BusinessDate)
ORDER BY r.SourceId, b.BusinessDate;
