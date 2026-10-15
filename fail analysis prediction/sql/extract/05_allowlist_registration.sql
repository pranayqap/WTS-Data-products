/*
    Fail Prediction Model -- register the extract procedures on the WSI DB Wrapper allowlist.
    Run in OperationsPortalDB after 00-04 are deployed, then call
    POST /wsi-db-wrapper/stored-procs/allowlist/refresh (or wait for the 15-minute reload).
*/

DECLARE @Procs TABLE (ProcedureName NVARCHAR(256) NOT NULL PRIMARY KEY);

INSERT @Procs (ProcedureName) VALUES
    (N'usp_FailModel_Extract_BetaTrades'),
    (N'usp_FailModel_Extract_BetaFailSnapshots'),
    (N'usp_FailModel_Extract_BetaSecurity'),
    (N'usp_FailModel_Extract_BetaSegregation'),
    (N'usp_FailModel_Extract_BetaCloseHistory'),
    (N'usp_FailModel_Extract_BetaIDConfirms'),
    (N'usp_FailModel_Extract_BetaThresholdHistory'),
    (N'usp_FailModel_Extract_P3Trades'),
    (N'usp_FailModel_Extract_P3FailSnapshots'),
    (N'usp_FailModel_Extract_P3RpeFails'),
    (N'usp_FailModel_Extract_P3EodPrices'),
    (N'usp_FailModel_Extract_CnsP3'),
    (N'usp_FailModel_Extract_CnsBeta'),
    (N'usp_FailModel_Extract_SmartLoanAvail'),
    (N'usp_FailModel_Extract_LoanetPositions'),
    (N'usp_FailModel_Extract_BorrowRates'),
    (N'usp_FailModel_Extract_Reg204'),
    (N'usp_FailModel_Extract_BusinessCalendar'),
    (N'usp_FailModel_Extract_StockLoanBorrow'),
    (N'usp_FailModel_Extract_BorrowForNeeds'),
    (N'usp_FailModel_Extract_Locates');

INSERT INTO dbo.WSIDBWrapperSPList (DatabaseName, SchemaName, ProcedureName, IsActive)
SELECT N'OperationsPortalDB', N'dbo', p.ProcedureName, 1
FROM @Procs AS p
WHERE NOT EXISTS (
    SELECT 1
    FROM dbo.WSIDBWrapperSPList AS w
    WHERE w.DatabaseName = N'OperationsPortalDB'
      AND w.SchemaName = N'dbo'
      AND w.ProcedureName = p.ProcedureName
);

SELECT DatabaseName, SchemaName, ProcedureName, IsActive, CreatedAt
FROM dbo.WSIDBWrapperSPList
WHERE ProcedureName LIKE N'usp_FailModel_Extract_%'
ORDER BY ProcedureName;
