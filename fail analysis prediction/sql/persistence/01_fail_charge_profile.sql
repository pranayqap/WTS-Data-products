-- Fail Analysis Predictive Model, phase 5 (plan 07): profile the severity target (NSCC CNS fail charges).
-- Read-only. Neither database is reachable through the WSI DB Wrapper (requests/wsi-data-access-request.md):
--   Part A runs on PostgreSQL prod-dbserver1 / RiskCredit (needs a read-only login).
--   Part B runs on SQL Server USDBLSTN / NSCC (needs a grant or a wrapper route, like BetaDailyArchive).
-- Outputs feed documentation section 11.6: history depth and retention, intraday slices, charge distribution,
-- and how the charge joins to fails (business date x CUSIP; the charge is per CUSIP, not per fail).

------------------------------------------------------------------------------------------------------------
-- Part A. PostgreSQL: RiskCredit.public.nsccintradaycnsfailspositions (primary target: final_fails_charge)
------------------------------------------------------------------------------------------------------------

-- A1. History depth and retention: is history kept, or only a rolling window? (Q6)
SELECT MIN(business_date) AS first_date, MAX(business_date) AS last_date,
       COUNT(DISTINCT business_date) AS dates, COUNT(*) AS row_count,
       MIN(fetched_at) AS first_fetch, MAX(fetched_at) AS last_fetch
FROM public.nsccintradaycnsfailspositions;

-- A2. Rows per business date and intraday slice: which slice_id is end of day?
SELECT business_date, slice_id, COUNT(*) AS row_count, COUNT(DISTINCT cusip) AS cusips,
       SUM(final_fails_charge) AS total_charge, MIN(fetched_at) AS fetched_from, MAX(fetched_at) AS fetched_to
FROM public.nsccintradaycnsfailspositions
WHERE business_date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY business_date, slice_id
ORDER BY business_date, slice_id;

-- A3. Charge distribution on the last slice per date, by side (failure_to) and days failed.
WITH last_slice AS (
    SELECT business_date, MAX(slice_id) AS slice_id
    FROM public.nsccintradaycnsfailspositions
    GROUP BY business_date
)
SELECT p.failure_to, LEAST(p.days_failed, 31) AS days_failed_capped, COUNT(*) AS row_count,
       SUM(CASE WHEN p.final_fails_charge > 0 THEN 1 ELSE 0 END) AS charged_rows,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY p.final_fails_charge) AS median_charge,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY p.final_fails_charge) AS p95_charge,
       SUM(p.final_fails_charge) AS total_charge,
       AVG(p.fails_charge_rate) AS mean_rate
FROM public.nsccintradaycnsfailspositions p
JOIN last_slice l ON l.business_date = p.business_date AND l.slice_id = p.slice_id
GROUP BY p.failure_to, LEAST(p.days_failed, 31)
ORDER BY p.failure_to, days_failed_capped;

-- A4. Key shape for the join to fails: one row per (business date, slice, CUSIP, sub-account)?
SELECT business_date, slice_id, cusip, sub_account_id, COUNT(*) AS dupes
FROM public.nsccintradaycnsfailspositions
GROUP BY business_date, slice_id, cusip, sub_account_id
HAVING COUNT(*) > 1
ORDER BY dupes DESC
LIMIT 50;

------------------------------------------------------------------------------------------------------------
-- Part B. SQL Server: NSCC.dbo.FailsDetailReport (fallback target: CNSFailCharge)
------------------------------------------------------------------------------------------------------------

-- B1. History depth, participants and charge distribution.
SELECT MIN(SettleDate) AS first_date, MAX(SettleDate) AS last_date, COUNT(DISTINCT SettleDate) AS dates,
       COUNT_BIG(*) AS row_count, COUNT(DISTINCT DTCNumber) AS dtc_numbers,
       SUM(CASE WHEN CNSFailCharge > 0 THEN 1 ELSE 0 END) AS charged_rows, SUM(CNSFailCharge) AS total_charge
FROM NSCC.dbo.FailsDetailReport WITH (NOLOCK);

-- B2. Daily rows and charges by DTC number (which participant numbers belong to Beta and to P3?).
SELECT CAST(SettleDate AS DATE) AS settle_date, DTCNumber, COUNT(*) AS row_count, COUNT(DISTINCT CUSIP) AS cusips,
       SUM(CNSFailCharge) AS total_charge, SUM(ABS(NetMarketAmount)) AS gross_market_amount
FROM NSCC.dbo.FailsDetailReport WITH (NOLOCK)
WHERE SettleDate >= DATEADD(DAY, -30, CAST(GETDATE() AS DATE))
GROUP BY CAST(SettleDate AS DATE), DTCNumber
ORDER BY settle_date, DTCNumber;

-- B3. Coverage of CNS fail CUSIPs by the firm's open fail lists (both books), last 60 days.
--     The share of charged CUSIPs with a matching open fail decides whether a fail-level severity target is
--     feasible or the target stays at CUSIP x business day.
WITH charges AS (
    SELECT CAST(SettleDate AS DATE) AS d, LEFT(CUSIP, 9) AS cusip9, SUM(CNSFailCharge) AS charge
    FROM NSCC.dbo.FailsDetailReport WITH (NOLOCK)
    WHERE SettleDate >= DATEADD(DAY, -60, CAST(GETDATE() AS DATE))
    GROUP BY CAST(SettleDate AS DATE), LEFT(CUSIP, 9)
),
fails AS (
    SELECT DISTINCT CAST(SnapShotDate AS DATE) AS d, LEFT(CUSIP, 9) AS cusip9
    FROM BetaDailyArchive.dbo.DailyArch_OpenFailReceiveDeliverInfo WITH (NOLOCK)
    WHERE SnapShotDate >= DATEADD(DAY, -60, CAST(GETDATE() AS DATE))
    UNION
    SELECT DISTINCT CAST(SnapShotDate AS DATE), LEFT(CUSIP_NUMBER, 9)
    FROM Phase3DB.dbo.EODFAIMST WITH (NOLOCK)
    WHERE SnapShotDate >= DATEADD(DAY, -60, CAST(GETDATE() AS DATE))
)
SELECT COUNT(*) AS charged_cusip_days,
       SUM(CASE WHEN f.cusip9 IS NOT NULL THEN 1 ELSE 0 END) AS with_open_fail,
       SUM(c.charge) AS total_charge,
       SUM(CASE WHEN f.cusip9 IS NOT NULL THEN c.charge ELSE 0 END) AS charge_with_open_fail
FROM charges c
LEFT JOIN fails f ON f.d = c.d AND f.cusip9 = c.cusip9;
