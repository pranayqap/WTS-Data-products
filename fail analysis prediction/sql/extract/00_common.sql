/*
    Fail Prediction Model -- proposed WSI extract objects (for WSI review)
    -----------------------------------------------------------------------
    Host database : OperationsPortalDB (same host as the allowlisted fail-analysis procedures,
                    which already read BetaDailyArchive / Phase3DB / ReplicateDB cross-database).
    Caller        : WSI DB Wrapper, POST /wsi-db-wrapper/stored-procs/execute
                    (databaseName = 'OperationsPortalDB', schema = 'dbo').
    Contract      : every extract procedure
                      - takes @FromDate DATE, @ToDate DATE, @RowCount INT OUTPUT
                      - reads only dated / archived tables, filtered on the table's as-of key
                      - returns native column names (the Python canonical layer maps them)
                      - excludes PII (no SSNTIN, no customer / account names)
                      - rejects windows longer than 31 days so each call stays well inside
                        the wrapper's 300-second command timeout
    Isolation     : default READ COMMITTED. WSI may substitute house NOLOCK conventions.
*/

CREATE OR ALTER FUNCTION dbo.ufn_FailModel_CusipUniverse (@FromDate DATE, @ToDate DATE)
RETURNS TABLE
AS
RETURN
(
    /* CUSIPs (first 9 characters) traded or failing in either book around the window.
       Security-level extracts are restricted to this universe to keep payloads small. */
    SELECT DISTINCT LEFT(LTRIM(RTRIM(u.CUSIP)), 9) AS CUSIP9
    FROM (
        SELECT t.CUSIP
        FROM BetaAccess.dbo.Trade AS t
        WHERE t.TradeDate >= DATEADD(DAY, -45, @FromDate) AND t.TradeDate < DATEADD(DAY, 1, @ToDate)
        UNION ALL
        SELECT t.CUSIP
        FROM BetaAccessArchive.dbo.Trade AS t
        WHERE t.TradeDate >= DATEADD(DAY, -45, @FromDate) AND t.TradeDate < DATEADD(DAY, 1, @ToDate)
        UNION ALL
        SELECT f.CUSIP
        FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo AS f
        WHERE f.SnapShotDate >= @FromDate AND f.SnapShotDate < DATEADD(DAY, 1, @ToDate)
        UNION ALL
        SELECT d.CUSIP_NUMBER
        FROM ReplicateDB.dbo.TRDDTL_REPL AS d
        WHERE d.TRADE_FULL_DATE >= DATEADD(DAY, -45, @FromDate) AND d.TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
        UNION ALL
        SELECT d.CUSIP_NUMBER
        FROM Phase3DB.dbo.TRDDTL_ARCH AS d
        WHERE d.TRADE_FULL_DATE >= DATEADD(DAY, -45, @FromDate) AND d.TRADE_FULL_DATE < DATEADD(DAY, 1, @ToDate)
        UNION ALL
        SELECT e.CUSIP_NUMBER
        FROM Phase3DB.dbo.EODFAIMST AS e
        WHERE e.SnapShotDate >= @FromDate AND e.SnapShotDate < DATEADD(DAY, 1, @ToDate)
    ) AS u
    WHERE u.CUSIP IS NOT NULL AND LTRIM(RTRIM(u.CUSIP)) <> ''
);
GO

CREATE OR ALTER PROCEDURE dbo.usp_FailModel_AssertWindow
    @FromDate DATE,
    @ToDate DATE,
    @MaxDays INT = 31
AS
BEGIN
    SET NOCOUNT ON;
    IF @FromDate IS NULL OR @ToDate IS NULL OR @ToDate < @FromDate
        THROW 50001, 'usp_FailModel: @FromDate and @ToDate are required and @ToDate must be >= @FromDate.', 1;
    IF DATEDIFF(DAY, @FromDate, @ToDate) > @MaxDays
        THROW 50002, 'usp_FailModel: window exceeds the maximum number of days for one call.', 1;
END;
GO
