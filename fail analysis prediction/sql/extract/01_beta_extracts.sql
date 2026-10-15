/*
    Fail Prediction Model -- Beta extracts (host: OperationsPortalDB). Requires 00_common.sql.
    Sources: BetaAccess, BetaAccessArchive, BetaDailyArchive.
*/

/* Trade universe. Live BetaAccess.dbo.Trade is unioned with BetaAccessArchive.dbo.Trade because
   ProcessTrade purges older rows to the archive; the live row wins on overlap. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BetaTrades
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    WITH src AS (
        SELECT t.ID, t.TradeDate, t.SettleDate, t.AsOfDate, t.TradeTimestamp, t.ExecutionTime,
               t.ControlNumber, t.BETAUniqueKey, t.AccountNumber, t.AccountType, t.AccountClass,
               t.NonCustomerAccount, t.SecurityNumber, t.CUSIP, t.SecurityType, t.Symbol,
               t.TradeQuantity, t.TradePrice, t.NetAmount, t.PurchaseOrSale, t.FirmCapacityCode,
               t.StandingInstructionCode, t.ContraDealerNumber, t.ContraBrokerNumber, t.foreign_curr,
               t.ProductCode, t.Cancelled, t.CancelReasonCode, t.RebilledTrade, t.SyndicateTrade,
               1 AS SourcePriority
        FROM BetaAccess.dbo.Trade AS t
        WHERE t.TradeDate >= @FromDate AND t.TradeDate < DATEADD(DAY, 1, @ToDate)
        UNION ALL
        SELECT a.ID, a.TradeDate, a.SettleDate, a.AsOfDate, a.TradeTimestamp, a.ExecutionTime,
               a.ControlNumber, a.BETAUniqueKey, a.AccountNumber, a.AccountType, a.AccountClass,
               a.NonCustomerAccount, a.SecurityNumber, a.CUSIP, a.SecurityType, a.Symbol,
               a.TradeQuantity, a.TradePrice, a.NetAmount, a.PurchaseOrSale, a.FirmCapacityCode,
               a.StandingInstructionCode, a.ContraDealerNumber, a.ContraBrokerNumber, a.foreign_curr,
               a.ProductCode, a.Cancelled, a.CancelReasonCode, a.RebilledTrade, a.SyndicateTrade,
               2 AS SourcePriority
        FROM BetaAccessArchive.dbo.Trade AS a
        WHERE a.TradeDate >= @FromDate AND a.TradeDate < DATEADD(DAY, 1, @ToDate)
    ),
    ranked AS (
        SELECT src.*, ROW_NUMBER() OVER (PARTITION BY src.ID, src.TradeDate ORDER BY src.SourcePriority) AS rn
        FROM src
    )
    SELECT ID, TradeDate, SettleDate, AsOfDate, TradeTimestamp, ExecutionTime, ControlNumber,
           BETAUniqueKey, AccountNumber, AccountType, AccountClass, NonCustomerAccount, SecurityNumber,
           CUSIP, SecurityType, Symbol, TradeQuantity, TradePrice, NetAmount, PurchaseOrSale,
           FirmCapacityCode, StandingInstructionCode, ContraDealerNumber, ContraBrokerNumber,
           foreign_curr, ProductCode, Cancelled, CancelReasonCode, RebilledTrade, SyndicateTrade
    FROM ranked
    WHERE rn = 1
    ORDER BY TradeDate, ID;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Label source and fail pressure. AccountName is deliberately excluded (PII). */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BetaFailSnapshots
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT f.SnapShotDate, f.FailUniqueId, f.ControlNumber, f.TradeDate, f.SettleDate, f.AsOfDate,
           f.AccountNumber, f.AccountType, f.SecurityNumber, f.CUSIP, f.SecurityTypeCode,
           f.ReceiveDeliverCode, f.Quantity, f.PartialQuantity, f.Amount, f.Price, f.NSCCNumber,
           f.DTC_CNS_Eligibility, f.AffirmedId, f.FailConditionCode, f.FailWho, f.DKCount,
           f.DTCReasonCode, f.StockLoanRate, f.UpdateDateTime
    FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo AS f
    WHERE f.SnapShotDate >= @FromDate AND f.SnapShotDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY f.SnapShotDate, f.FailUniqueId;

    SET @RowCount = @@ROWCOUNT;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BetaSecurity
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT s.SnapShotDate, s.SecurityNumber, s.CUSIP, s.Symbol, s.SecurityTypeCode, s.SecurityClass,
           s.LastClosePrice, s.LastCloseDate, s.BidPrice, s.AskPrice, s.RoundLotQuantity,
           s.DTCCNSEligibilityCode, s.NSCCEligible, s.DTCChillDeposit, s.DTCChillCOD, s.DTCChillWdl,
           s.DTCChilldo, s.DTCChillPledge, s.DTCChillSeg, s.DTCChillFuture, s.DTCfrozen,
           s.MaturityDate, s.IPOPrice, s.BondFactor, s.UpdateDateTime
    FROM BetaDailyArchive.dbo.DailyArch_Security AS s
    WHERE s.SnapShotDate >= @FromDate AND s.SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(s.CUSIP)), 9))
    ORDER BY s.SnapShotDate, s.SecurityNumber;

    SET @RowCount = @@ROWCOUNT;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BetaSegregation
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT g.SnapShotDate, g.SecurityNumber, g.CUSIP, g.Required, g.Available, g.StartOfDayDeficitQty,
           g.StockLoanQty, g.StockBorrowQty, g.DealerFailDeliverQty, g.DealerFailReceiveQty,
           g.CUST_FR, g.CUST_FD, g.CNS_1, g.CNS_2, g.InventoryLongQuantity, g.InventoryShortQuantity,
           g.LoannetBorrowQty, g.LoannetLoanQty, g.FrozenCode, g.NoLoanInd, g.UpdateDateTime
    FROM BetaDailyArchive.dbo.DailyArch_SegregationAnalysisInfo AS g
    WHERE g.SnapShotDate >= @FromDate AND g.SnapShotDate < DATEADD(DAY, 1, @ToDate)
      AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                  WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(g.CUSIP)), 9))
    ORDER BY g.SnapShotDate, g.SecurityNumber;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Close price and volume history. Replaces DailyArch_SecurityPrice, whose LastClosePrice is never loaded. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BetaCloseHistory
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT h.TradeDate, h.SecurityNumber, h.ClosePrice, h.ClosePriceDate, h.CloseVolume
    FROM BetaAccessArchive.dbo.SecurityCloseHistory AS h
    WHERE h.TradeDate >= @FromDate AND h.TradeDate < DATEADD(DAY, 1, @ToDate)
      AND h.SecurityNumber IN (
            SELECT s.SecurityNumber
            FROM BetaDailyArchive.dbo.DailyArch_Security AS s
            WHERE s.SnapShotDate >= @FromDate AND s.SnapShotDate < DATEADD(DAY, 1, @ToDate)
              AND EXISTS (SELECT 1 FROM dbo.ufn_FailModel_CusipUniverse(@FromDate, @ToDate) AS u
                          WHERE u.CUSIP9 = LEFT(LTRIM(RTRIM(s.CUSIP)), 9)))
    ORDER BY h.TradeDate, h.SecurityNumber;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Institutional confirms. Each confirm is archived once (PK BETAUniqueKey), so SnapshotDate is the
   only knowledge time available; the model only uses rows with SnapshotDate <= scoring date. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BetaIDConfirms
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate;

    SELECT c.SnapshotDate, c.BETAUniqueKey, c.BetaTradeUniqueKey, c.TradeDate, c.SettleDate, c.CUSIP,
           c.Quantity, c.AccountNumber, c.MatchedIndicator, c.ConfirmAffirmIndicator,
           c.AffirmPartyIndicator, c.DTCEligibleIndicator, c.CancelIndicator, c.StatusIndicator,
           c.ConfirmProcessDate
    FROM BetaDailyArchive.dbo.DailyArch_IDConfirms AS c
    WHERE c.SnapshotDate >= @FromDate AND c.SnapshotDate < DATEADD(DAY, 1, @ToDate)
    ORDER BY c.SnapshotDate, c.BETAUniqueKey;

    SET @RowCount = @@ROWCOUNT;
END;
GO

/* Reg SHO threshold flag change history. Knowledge time is the later of EffectiveDate and
   AddedTimestamp, so back-dated changes are not visible before they were recorded. The window
   cap is lifted (@MaxDays = 36500) so the first call can load full history. */
CREATE OR ALTER PROCEDURE dbo.usp_FailModel_Extract_BetaThresholdHistory
    @FromDate DATE,
    @ToDate DATE,
    @RowCount INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.usp_FailModel_AssertWindow @FromDate, @ToDate, @MaxDays = 36500;

    SELECT x.SecurityNumber, x.EffectiveDate, x.AddedTimestamp, x.ThresholdInd, x.ThresholdTerm,
           x.UpdateDateTime
    FROM BetaAccess.dbo.SecurityExtensionHistory AS x
    WHERE TRY_CONVERT(DATE, x.EffectiveDate) >= @FromDate
      AND TRY_CONVERT(DATE, x.EffectiveDate) < DATEADD(DAY, 1, @ToDate)
    ORDER BY x.SecurityNumber, x.EffectiveDate, x.ID;

    SET @RowCount = @@ROWCOUNT;
END;
GO
