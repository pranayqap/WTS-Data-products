# 05 — Phase 3: Modeling, Validation and Calibration

- **Status:** Done 2026-10-14 (benchmarks, LightGBM, folds, calibration, reason codes, bundle, model card and tests complete; validated on synthetic data, documentation section 11.4: deliver and receive beat the rule, and the deliver ECE misses 0.02 because of synthetic base-rate drift, reported as a fail. Live training waits on Q1)
- **Todo:** `phase3-model`
- **Depends on:** [04-phase2-features.md](04-phase2-features.md)

## Objective

Train and validate the deliver and receive new-fail models, pooled across Beta and P3. Show they beat simple rules on out-of-time data, and produce calibrated probabilities with reason codes that operations can act on.

## Steps

1. **Benchmarks**
   - **Rule baseline:** the score is 1 if the CUSIP already has an open fail in the same direction as of D, or the Beta box is in deficit. Ties are ranked by notional. This mirrors how ops triage today.
   - **Logistic regression** on standardized, median-imputed features, with class weighting.
2. **Primary model:** a LightGBM binary classifier.
   - Categorical features are passed natively.
   - Imbalance is handled with `scale_pos_weight`.
   - Early stopping uses the validation fold.
   - Hyper-parameters are tuned only on the rolling folds, never on the holdout.
3. **Rolling-origin validation**
   - Six monthly folds, ordered by S. Each fold trains on all prior data up to the fold start minus a purge gap of 5 business days, then validates on the fold month.
   - A final out-of-time holdout covers the last 3 months.
4. **Calibration:** isotonic regression, fitted on the pooled out-of-fold predictions of the last two folds. It falls back to sigmoid when there are fewer than 500 positives.
5. **Metrics:** reported per fold and on the holdout.
   - PR-AUC and ROC-AUC.
   - Brier score and expected calibration error.
   - Precision and recall at daily top-K, for K in {25, 50, 100}.
   - Dollar-weighted recall at K: the share of failed notional caught.
6. **Slices:** book, direction, product group, counterparty type and notional band.
7. **Reason codes:** LightGBM native SHAP contributions (`pred_contrib=True`). The top three features per trade are mapped to plain-language descriptions in the feature catalog.
8. **Model card:** intended use, training window, metrics, limits and owner, written into the documentation.

## Acceptance criteria (holdout)

- LightGBM beats the rule baseline on both PR-AUC and precision at K = 50, for the deliver model.
- Calibration ECE ≤ 0.02, or within 25% of the base rate if the base rate is below 2%.
- No fold's PR-AUC is below 70% of the fold average.

## Outputs

- `src/failmodel/models/`:
  - `baseline.py`
  - `train.py`
  - `validation.py`
  - `calibration.py`
  - `explain.py`
  - `metrics.py`
- `artifacts/<model_version>/`, git-ignored, holding:
  - model files
  - calibrator
  - feature list
  - training reference distributions
  - metrics JSON
- The CLI command `python -m failmodel.cli train`.

## Documentation sections updated

Modeling approach, Validation and metrics, Results — Phase 3, Limitations and model risk, Changelog.
