# 06 — Phase 4: Shadow Scoring and Monitoring

- **Status:** Done 2026-10-14 (scoring, completeness gate, monitoring, alerts, go-live checks, score table DDL, CLI and tests complete. An out-of-sample shadow simulation on synthetic data gives exact training/serving parity and catches the planted deliver regime change; see documentation sections 11.5 and 12. The daily incremental is 18 calls, not 16, because of sources added in phases 0a and 1. Live shadow runs wait on Q1)
- **Todo:** `phase4-shadow`
- **Depends on:** [05-phase3-modeling.md](05-phase3-modeling.md)

## Objective

Score every pending trade each night in shadow mode: scores are produced and stored but not shown to users. Monitor data, scores and realized performance until the go-live criteria are met. Then hand the score table to the Ops Hub Fail Analysis tool.

## Daily run (D = today, 22:30 ET)

1. **Pull extracts.** Run the daily incremental extracts for D (16 wrapper calls).
2. **Check completeness.** Each source must have rows for its required as-of date, and the row count must be within ±50% of its 20-day median. A failing source is marked degraded and its features are set to missing; the run continues, and the failure is recorded in the run log.
3. **Build the population and features.** The population is all live trades with S = next business day after D, entered by D, using the phase-1 rules. Features are built with the phase-2 builder.
4. **Score.** Load the active model version, produce calibrated probabilities, rank within book and direction, and attach the top three reason codes.
5. **Write output.** Rows go to `artifacts/scores/score_date=D/scores.parquet`, and in production to the score table `ops.fail_prediction_scores` (`sql/scores/fail_prediction_scores.sql`).
6. **Close the loop.** On D+1, after the S snapshot lands, join realized labels to D's scores and append daily performance to the monitoring log.

## Monitoring

| Signal | Metric | Alert threshold |
|---|---|---|
| Feature drift | PSI against the training reference distribution | PSI > 0.25 on any top-20 feature |
| Score drift | PSI of calibrated scores | PSI > 0.2 |
| Data completeness | Source row counts against the 20-day median | Outside ±50%, or missing |
| Realized performance | Daily precision at K = 50, rolling 20-day PR-AUC | Rolling PR-AUC below 80% of holdout PR-AUC |
| Calibration | Rolling 20-day predicted vs. observed fail rate | Ratio outside 0.7 to 1.3 |

## Go-live criteria

- At least 30 business days of shadow scoring with no unresolved critical alerts.
- Rolling realized PR-AUC and precision at K = 50 within 20% of the holdout values.
- Ops reviews a sample of the top-50 lists and their reason codes, and confirms they can act on them.
- Integration path agreed with the WTS data team: the score table lands in Redshift `ops` and is registered in `docs/integrations/data-sources.yaml`. The Ops Hub reads it through `queryAnalyticsWarehouse()`.
- Retraining cadence agreed: quarterly by default, or early on a drift alert.

## Outputs

- `src/failmodel/scoring.py`: daily population, scoring, reason codes, output writer.
- `src/failmodel/monitoring.py`: PSI, completeness, realized-performance log.
- `sql/scores/fail_prediction_scores.sql`: the score table DDL.
- CLI commands `python -m failmodel.cli score --date D` and `python -m failmodel.cli monitor --date D`.

## Documentation sections updated

Deployment and monitoring, Results — Phase 4, Open questions, Changelog.
