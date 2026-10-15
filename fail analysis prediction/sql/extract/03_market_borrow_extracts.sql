/*
    Fail Prediction Model -- CNS, stock loan, borrow and Reg SHO extracts (host: OperationsPortalDB).
    Requires 00_common.sql. Sources: Phase3DB, NSCC, ReportDB.
*/

/* P3 CNS positions (participant 8199). ClosingNetPosition < 0 = CNS fail to deliver. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_CnsP3
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT c.SettleDate AS PositionDate, c.Cycle, c.CUSIP, c.ClosingNetPosition, c.NetSettlingTrades,
           c.MarketValue, c.ParticipantClearingNum, c.ParticipantSubAccount, c.UpDateDateTime
    FROM Phase3DB.dbo.CNS_8199_S1_Data AS c
    WHERE c.SettleDate >= @FromDate AND c.SettleDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(c.CUSIP)), 9))
    ORDER BY c.SettleDate, c.Cycle, c.CUSIP;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Beta CNS positions. NSCC.CNS_S1_Data names its date column TradeDate, but its loader treats it
   as the settle/report date; profiling check P6 confirms the semantics before use. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_CnsBeta
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT c.TradeDate AS PositionDate, c.Cycle, c.CUSIP, c.ClosingNetPosition, c.NetSettlingTrades,
           c.MarketValue, c.ParticipantClearingNum, c.ParticipantSubAccount, c.UpDateDateTime
    FROM NSCC.dbo.CNS_S1_Data AS c
    WHERE c.TradeDate >= @FromDate AND c.TradeDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(c.CUSIP)), 9))
    ORDER BY c.TradeDate, c.Cycle, c.CUSIP;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Lendable supply. FileDate is the as-of date; TradeDate is the next business day it applies to. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_SmartLoanAvail
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT s.FileDate, s.TradeDate, s.CUSIP, s.TotalAvailable, s.AvailableFromFirm, s.AvailableFromCustomer,
           s.AvailableFromNonCustomer, s.AvailableFromBankLoan, s.AvailableFromReturn, s.OtherAvailability,
           s.MarketPriceClosing, s.SecurityType, s.LastUpdateDateTime
    FROM ReportDB.dbo.SmartLoanSLAvail AS s
    WHERE s.FileDate >= @FromDate AND s.FileDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(s.CUSIP)), 9))
    ORDER BY s.FileDate, s.CUSIP;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Street tightness, aggregated to CUSIP x TradeDate x BorrowLoan to keep payloads small. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_LoanetPositions
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT l.TradeDate, l.CUSIP, l.BorrowLoan,
           SUM(CAST(l.Quantity AS BIGINT)) AS TotalQuantity,
           COUNT(*) AS ContractCount,
           COUNT(DISTINCT l.ContraParty) AS ContraCount,
           AVG(l.Rate) AS AvgRate,
           AVG(l.Rebate) AS AvgRebate,
           MIN(l.Rebate) AS MinRebate
    FROM ReportDB.dbo.LoanetOpenPositions AS l
    WHERE l.TradeDate >= @FromDate AND l.TradeDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(l.CUSIP)), 9))
    GROUP BY l.TradeDate, l.CUSIP, l.BorrowLoan
    ORDER BY l.TradeDate, l.CUSIP, l.BorrowLoan;

    SET @RowCount = @@ROWCOUNT;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BorrowRates
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT r.FileDate, r.SecurityNumber, r.CUSIP, r.NumberOfBorrows, r.BorrowQuantity, r.BorrowVWAR,
           r.BorrowCost
    FROM ReportDB.dbo.StockBorrowInterestRate AS r
    WHERE r.FileDate >= @FromDate AND r.FileDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(r.CUSIP)), 9))
    ORDER BY r.FileDate, r.SecurityNumber;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Published 204 fail grid. Numerics are stored as varchar and converted here; AccountShortName is excluded. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_Reg204
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT r.Source, r.ReportDate, r.File204Date, r.SettleDate, r.CUSIP, r.SecurityType, r.Category,
           r.AccountType, r.BorrowDesc, r.ThresholdIndex,
           TRY_CONVERT(BIGINT, r.SOD_CNS_FailDayQty) AS SOD_CNS_FailDayQty,
           TRY_CONVERT(DECIMAL(20, 2), r.FailAmount) AS FailAmount,
           TRY_CONVERT(BIGINT, r.SettleQty) AS SettleQty,
           r.IsWarrant
    FROM ReportDB.dbo.CNS204FailSettlementData AS r
    WHERE r.ReportDate >= @FromDate AND r.ReportDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY r.ReportDate, r.CUSIP;

    SET @RowCount = @@ROWCOUNT;
END;
GO
