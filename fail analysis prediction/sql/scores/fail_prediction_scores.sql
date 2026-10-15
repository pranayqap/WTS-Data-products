-- Fail Analysis Predictive Model: score table for the Ops Hub Fail Analysis tool (plan 06).
-- Target: Redshift schema `ops` (read by the Ops Hub through queryAnalyticsWarehouse()).
-- Loaded nightly from artifacts/scores/score_date=<D>/scores.parquet (COPY ... FORMAT AS PARQUET).
-- One row per scored trade per run. The model is advisory: shadow_mode = TRUE until go-live sign-off.
-- No PII: no SSN/TIN, account names, customer names or rep codes are written.

CREATE TABLE IF NOT EXISTS ops.fail_prediction_scores (
    model_version      VARCHAR(64)      NOT NULL,   -- bundle version, e.g. fm-v1-20261014
    run_id             VARCHAR(32)      NOT NULL,
    run_timestamp      TIMESTAMP        NOT NULL,   -- ET, when the nightly run produced the row
    scoring_date       DATE             NOT NULL,   -- D: business day before S
    settle_date        DATE             NOT NULL,   -- S: contractual settle date
    book               CHAR(1)          NOT NULL,   -- B = Beta, P = P3 (BR reserved for Broadridge)
    direction          CHAR(1)          NOT NULL,   -- D = deliver, R = receive (firm side)
    trade_key          VARCHAR(256)     NOT NULL,   -- canonical trade key (B|ControlNumber|... or P|...)
    unique_ref         VARCHAR(128),                -- Beta BETAUniqueKey; P3 trade key
    account            VARCHAR(32),
    cusip9             CHAR(9),
    product_group      VARCHAR(8),                  -- EQ, CORP, MUNI, FED, OTHER
    counterparty_type  VARCHAR(16),                 -- Broker, Customer, Suspense
    quantity           DECIMAL(28, 5),
    notional           DECIMAL(28, 2),
    fail_probability   DOUBLE PRECISION NOT NULL,   -- calibrated P(trade is on the Age-1 fail list for S)
    raw_score          DOUBLE PRECISION,            -- uncalibrated model output (diagnostics)
    risk_rank          INTEGER,                     -- 1 = riskiest within (scoring_date, book, direction)
    reason_1           VARCHAR(64),
    reason_1_text      VARCHAR(256),
    reason_1_contrib   REAL,                        -- SHAP contribution in log-odds
    reason_2           VARCHAR(64),
    reason_2_text      VARCHAR(256),
    reason_2_contrib   REAL,
    reason_3           VARCHAR(64),
    reason_3_text      VARCHAR(256),
    reason_3_contrib   REAL,
    degraded_sources   VARCHAR(512),                -- sources that failed the completeness gate (features masked)
    shadow_mode        BOOLEAN          NOT NULL DEFAULT TRUE,
    PRIMARY KEY (scoring_date, model_version, trade_key)
)
DISTKEY (settle_date)
SORTKEY (scoring_date, book, direction, risk_rank);

-- Latest run per scoring date and model version (a rerun replaces the earlier run for that date).
CREATE OR REPLACE VIEW ops.v_fail_prediction_scores_latest AS
SELECT s.*
FROM ops.fail_prediction_scores s
JOIN (
    SELECT scoring_date, model_version, MAX(run_timestamp) AS run_timestamp
    FROM ops.fail_prediction_scores
    GROUP BY scoring_date, model_version
) latest
  ON latest.scoring_date = s.scoring_date
 AND latest.model_version = s.model_version
 AND latest.run_timestamp = s.run_timestamp;

-- Daily realized performance from the monitor (artifacts/monitoring/performance_daily.csv).
CREATE TABLE IF NOT EXISTS ops.fail_prediction_monitoring (
    model_version      VARCHAR(64)      NOT NULL,
    scoring_date       DATE             NOT NULL,
    direction          CHAR(1)          NOT NULL,
    scored             INTEGER,
    labelled           INTEGER,
    unlabelled         INTEGER,          -- scored trades with no label (for example cancelled after D)
    fails              INTEGER,
    observed_rate      DOUBLE PRECISION,
    mean_predicted     DOUBLE PRECISION,
    precision_at_50    DOUBLE PRECISION,
    pr_auc             DOUBLE PRECISION,
    PRIMARY KEY (model_version, scoring_date, direction)
)
DISTSTYLE ALL
SORTKEY (scoring_date);

-- Example Ops Hub read: tomorrow's top 50 deliver risks per book.
-- SELECT book, risk_rank, trade_key, account, cusip9, notional, fail_probability,
--        reason_1_text, reason_2_text, reason_3_text
-- FROM ops.v_fail_prediction_scores_latest
-- WHERE scoring_date = CURRENT_DATE AND direction = 'D' AND risk_rank <= 50
-- ORDER BY book, risk_rank;
