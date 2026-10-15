/*
    Fail Prediction Model -- calendar and conditional extracts (host: OperationsPortalDB).
    Requires 00_common.sql. Conditional sources are only used after profiling check P3 shows they are
    computed once, before 22:30 ET on the scoring date, and never re-run for a past settle date.
*/

CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BusinessCalendar
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate, @MaxDays = 36500;

    SELECT d.ActualDate, d.FileDate, d.SettleDate, d.NextSettleDate, d.IsMonthEnd, d.IsQuarterEnd,
           d.IsYearEnd, d.DataComplete, d.P3DataComplete
    FROM ControlDB.dbo.DateMaster AS d
    WHERE d.ActualDate >= @FromDate AND d.ActualDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY d.ActualDate;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Conditional: OpenReceiveDeliver and CNSNightlySettled are derived from the open-fails table at run time. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_StockLoanBorrow
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT b.SettleDate, b.Cusip, b.SecurityNumber, b.Available, b.NetAvailable, b.Required, b.Inventory,
           b.Excess, b.StockBorrow, b.StockLoan, b.CNSLoan, b.OpenReceiveDeliver, b.ReceiveDeliverTomorrow,
           b.CNSNightlySettled, b.CallBackTomorrow
    FROM NSCC.dbo.StockLoanBorrow AS b
    WHERE b.SettleDate >= @FromDate AND b.SettleDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY b.SettleDate, b.SecurityNumber;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Conditional: OpenFailToDeliver is derived from the open-fails table at run time. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BorrowForNeeds
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT n.Source, n.SettleDate, n.CUSIP, n.SecurityNumber, n.GrossDeliver, n.Available, n.GrossNeeds,
           n.DeliveryNeed, n.Deficit, n.OpenFailToDeliver, n.StockLoanBorrow, n.DVPDeliver, n.ExClearing
    FROM NSCC.dbo.BorrowForNeeds AS n
    WHERE n.SettleDate >= @FromDate AND n.SettleDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY n.SettleDate, n.SecurityNumber;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Short-sale locates, aggregated by symbol and day. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_Locates
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT l.TradeDate, l.Symbol,
           SUM(l.ShortSellQty) AS ShortSellQty,
           SUM(l.LocateQty) AS LocateQty,
           SUM(l.RequestedQty) AS RequestedQty,
           MAX(l.BorrowStatus) AS MaxBorrowStatus,
           COUNT(*) AS LocateCount
    FROM ReportDB.dbo.ShortSellStockLocate_Archive AS l
    WHERE l.TradeDate >= @FromDate AND l.TradeDate < DATEADD(DAY, 1, @ToDate)
    GROUP BY l.TradeDate, l.Symbol
    ORDER BY l.TradeDate, l.Symbol;

    SET @RowCount = @@ROWCOUNT;
END;
GO
