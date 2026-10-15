/*
    Phase 1 -- Age-1 fail counts per snapshot date, book and direction, using the filters of
    GetBetaFailAnalysisData_Summary and GetP3FailAnalysisData_Summary. Read-only, no PII.
    Compare with failmodel.labels.reconcile (CLI: failmodel labels, file age1_reconciliation_daily.csv),
    whose category columns sum to the same Age-1 total.

    Age in the report = DATEDIFF(day, SettleDate, SnapShotDate) + 1, so Age 1 means SnapShotDate = SettleDate.
    Beta: AccountType <> 8 and SecurityTypeCode <> 'P' on the fail row; the report also requires a row in the
          current BetaAccess.dbo.Security with SecurityTypeCode <> 'P' (ReportEquivalent column).
    P3:   ACCT_TYPE NOT IN ('2','3'); SEC_TYPE from current ReplicateDB.dbo.SECBAS_REPL NOT IN ('5','6') or NULL.
          Report source: EODFAIMST before 2025-08-14, RPE200/RPE201 from that date (GetP3FailAnalysisData_Report);
          the Summary procedure reads the RPE view for every date. Both sources are counted.
          Direction: QUANTITY < 0 or NET_AMOUNT < 0 -> Receive, otherwise Deliver.
*/
DECLARE @FromDate DATE = '2024-05-28';
DECLARE @ToDate   DATE = DATEADD(DAY, -1, CAST(GETDATE() AS DATE));
DECLARE @Switch   DATE = '2025-08-14';

/* Beta */
SELECT CAST(f.SnapShotDate AS DATE) AS SnapDate, 'B' AS Book,
       CASE f.ReceiveDeliverCode WHEN 0 THEN 'D' WHEN 1 THEN 'R' END AS Direction,
       COUNT_BIG(*) AS Age1Fails,
       SUM(CASE WHEN sec.SecurityTypeCode <> 'P' THEN 1 ELSE 0 END) AS Age1FailsReportEquivalent,
       SUM(CASE WHEN f.ControlNumber = 0 THEN 1 ELSE 0 END) AS ZeroControlFails
FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo AS f
LEFT JOIN BetaAccess.dbo.Security AS sec ON sec.SecurityNumber = f.SecurityNumber AND sec.CUSIP = f.CUSIP
WHERE f.SnapShotDate >= @FromDate AND f.SnapShotDate < DATEADD(DAY, 1, @ToDate)
  AND DATEDIFF(DAY, f.SettleDate, f.SnapShotDate) = 0
  AND f.AccountType <> 8 AND f.SecurityTypeCode <> 'P'
GROUP BY CAST(f.SnapShotDate AS DATE), f.ReceiveDeliverCode
ORDER BY SnapDate, Direction;

/* P3: EODFAIMST (label source, and the report source before the switch) */
SELECT CAST(e.SnapShotDate AS DATE) AS SnapDate, 'P' AS Book,
       CASE WHEN e.QUANTITY < 0 OR e.NET_AMOUNT < 0 THEN 'R' ELSE 'D' END AS Direction,
       e.RECORD_TYPE,
       CASE WHEN e.SnapShotDate < @Switch THEN 'report_source' ELSE 'label_source_only' END AS Role,
       COUNT_BIG(*) AS Age1Fails
FROM Phase3DB.dbo.EODFAIMST AS e
LEFT JOIN ReplicateDB.dbo.SECBAS_REPL AS sb ON sb.CUSIP_NUMBER = e.CUSIP_NUMBER
WHERE e.SnapShotDate >= @FromDate AND e.SnapShotDate < DATEADD(DAY, 1, @ToDate)
  AND DATEDIFF(DAY, e.SETTLE_DATE, e.SnapShotDate) = 0
  AND e.ACCT_TYPE NOT IN ('2', '3')
  AND (sb.SEC_TYPE IS NULL OR sb.SEC_TYPE NOT IN ('5', '6'))
GROUP BY CAST(e.SnapShotDate AS DATE), CASE WHEN e.QUANTITY < 0 OR e.NET_AMOUNT < 0 THEN 'R' ELSE 'D' END,
         e.RECORD_TYPE, CASE WHEN e.SnapShotDate < @Switch THEN 'report_source' ELSE 'label_source_only' END
ORDER BY SnapDate, Direction, e.RECORD_TYPE;

/* P3: RPE200/RPE201 (report source from the switch) */
SELECT CAST(r.SnapshotDate AS DATE) AS SnapDate, 'P' AS Book,
       CASE WHEN r.Quantity < 0 OR r.NetAmount < 0 THEN 'R' ELSE 'D' END AS Direction,
       r.FileSource, COUNT_BIG(*) AS Age1Fails
FROM Phase3DB.dbo.vw_RPE200_RPE201_Combined AS r
LEFT JOIN ReplicateDB.dbo.SECBAS_REPL AS sb ON sb.CUSIP_NUMBER = r.CUSIP
WHERE r.SnapshotDate >= @FromDate AND r.SnapshotDate < DATEADD(DAY, 1, @ToDate)
  AND DATEDIFF(DAY, r.SettleDate, r.SnapshotDate) = 0
  AND r.AccountType NOT IN (2, 3)
  AND (sb.SEC_TYPE IS NULL OR sb.SEC_TYPE NOT IN ('5', '6'))
GROUP BY CAST(r.SnapshotDate AS DATE), CASE WHEN r.Quantity < 0 OR r.NetAmount < 0 THEN 'R' ELSE 'D' END, r.FileSource
ORDER BY SnapDate, Direction, r.FileSource;

/*
    Spot check through the WSI wrapper (both procedures are already allowlisted). For one business day,
    the Age '1' rows (Type F/R and F/D, TotalFails) should equal the Beta Age1FailsReportEquivalent and,
    after the switch, the RPE Age1Fails above:

    EXEC OperationsPortalDB.dbo.GetBetaFailAnalysisData_Summary @frmdate = '2024-06-05', @todate = '2024-06-05', @AgeFilter = '1';
    EXEC OperationsPortalDB.dbo.GetP3FailAnalysisData_Summary   @frmdate = '2025-09-03', @todate = '2025-09-03', @AgeFilter = '1';
*/
