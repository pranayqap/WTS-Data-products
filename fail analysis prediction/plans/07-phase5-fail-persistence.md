# 07 — Phase 5: Fail Persistence and Close-out Risk

- **Status:** Done 2026-10-14 (fail tracking, persistence and close-out labels, fail-state and cure-history features, age-only benchmark, training on the phase-3 engine through a `Task`, reconciliation, CLI and tests complete. On synthetic data the labels reconcile with snapshot counts and `still_open_h3` beats age-only in both directions; see documentation section 11.6. Tasks are named `still_open_h{h}` rather than `persistence_h{h}`. The Rule 204 label uses S+3 for all deliver equity fails until a short-sale marker is found (Q11). The severity exit criterion is blocked on `RiskCredit` and `NSCC` access; its profiling SQL is ready in `sql/persistence/01_fail_charge_profile.sql`, and retention is still to be confirmed with the RiskCredit owners (Q6))
- **Todo:** `phase5-persistence`
- **Depends on:** [03-phase1-labels.md](03-phase1-labels.md) (fail keys), [04-phase2-features.md](04-phase2-features.md) (features), [05-phase3-modeling.md](05-phase3-modeling.md) (training engine)

## Objective

For a fail that is already open, predict whether it will still be open after a set horizon, and whether it is on track to breach a close-out deadline. This is the user's original "still open on the next snapshot" idea, made precise. A severity target (fail charges) ranks fails by cost.

## Unit and labels

- **Unit:** one open fail on snapshot date D, with business-day age a ≥ 1. Rows come from both books, keyed by the phase-1 fail key.
- **Persistence labels:** `still_open_h` for h in {1, 3, 5} business days. Each is 1 if the same fail key is in the snapshot for D+h, 0 if that snapshot exists without it, and NULL if the snapshot is missing.
- **Close-out labels:**
  - **Reg SHO Rule 204:** CNS fail-to-deliver positions must be closed out by market open on S+1 for short sales, and S+3 for long sales and bona fide market-making. The label is that the fail is still open at the deadline that applies; the deadline is approximated by S+3 business days when sale type is unknown.
  - **Municipal:** the fail is still open at 10 business days, per the firm's close-out policy (OPS.WSP.F.14).
- **Severity target** (regression, CUSIP × business day): `nsccintradaycnsfailspositions.final_fails_charge`, with `NSCC.FailsDetailReport.CNSFailCharge` as a fallback.

## Features

- Everything from phase 2, re-anchored to the snapshot date D.
- Fail state as of D. This is allowed here because the fail already exists at D:
  - age in business days;
  - remaining quantity versus original (partial cures);
  - DK count and reason code at D;
  - quantity change since the previous snapshot.
- Cure history: the smoothed rate at which this counterparty's and this CUSIP's fails cured within h days, using fails whose horizon closed by D.

## Steps

1. Track fails across snapshots by fail key. Compute business-day age and next-snapshot presence.
2. Build the persistence labels and close-out labels, with missing snapshots treated as unknown.
3. Reuse the phase-2 builder with fail-state features added. Reuse the phase-3 engine, with the task set to `persistence_h{h}`.
4. Evaluate with the same rolling-origin scheme. Slice by age bucket (1, 2–5, 6–30, 30+) to match the Fail Analysis report.

## Outputs

- `src/failmodel/persistence.py`: fail tracking, persistence and close-out labels, fail-state features.
- Task configuration in `src/failmodel/config.py`.
- Tests in `tests/test_persistence.py`.

## Exit criteria

- Persistence labels are reconciled with snapshot counts: fails open at D equal those still open at D+1 plus those cured.
- The `still_open_h3` model beats an age-only baseline on holdout PR-AUC.
- The severity target is profiled. The intraday table's retention is confirmed with the RiskCredit owners.

## Documentation sections updated

Scope, Prediction unit and label (phase 5), Results — Phase 5, Changelog.
