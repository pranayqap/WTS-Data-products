import pandas as pd

from failmodel.profiling import p3_rpe_coverage, p8_switch, run_profile, to_markdown


def _checks(result):
    return {c.id: c for c in result.checks}


def test_profile_recovers_planted_timing_and_semantics(raw_frames, settings):
    result = run_profile(settings, raw_frames)
    recs = result.recommendations
    assert recs["beta_snapshot_offset_bd"][0] == 0
    assert recs["label_availability_lag_bd"][0] == 1
    assert recs["cns_eod_cycle"][0] == "C02"
    assert recs["p3_trade_record_types"][0] == ("F",)
    assert recs["lag.beta_security"][0] == 1
    assert recs["lag.smartloan_avail"][0] == 0


def test_profile_flags_missing_snapshots_and_blockers(raw_frames, settings):
    checks = _checks(run_profile(settings, raw_frames))
    history = checks["P1"].tables["history"].set_index("source")
    assert history.loc["beta_fail_snapshots", "missing_business_days"] == 2
    assert history.loc["p3_fail_snapshots", "missing_business_days"] >= 1
    assert history.loc["stock_loan_borrow", "status"] == "no extract"
    assert checks["P3"].status == "blocked"
    assert checks["P8"].status == "blocked"
    assert checks["P5"].status == "ok"


def _eod(snap, acct, tran, cusip=" 123456789  "):
    return {"SnapShotDate": snap, "SETTLE_DATE": snap, "ACCT_NBR": acct, "TRAN_NBR": tran,
            "TRADE_FULL_DATE": "2025-08-14", "MARKET": "1", "CUSIP_NUMBER": cusip, "ACCT_TYPE": "1"}


def _rpe(snap, acct, tran, cusip="123456789"):
    return {"SnapShotDate": snap, "SettleDate": snap, "AccountNumber": acct, "TranNbr": tran,
            "TradeDate": "2025-08-14", "Market": "1", "CUSIP": cusip, "AccountType": 1}


def test_p8_rpe_coverage_uses_view_join_key():
    eod = pd.DataFrame([_eod("2025-08-15", "111", "A1"), _eod("2025-08-15", "222", "A2"),
                        _eod("2025-08-13", "333", "A3")])
    rpe = pd.DataFrame([_rpe("2025-08-15", "111", "A1"), _rpe("2025-08-15", "444", "A4")])
    coverage = p3_rpe_coverage(eod, rpe).set_index("measure")
    assert coverage.loc["rpe_age1_keys_found_in_eodfaimst", "match_share"] == 0.5
    assert coverage.loc["eodfaimst_age1_keys_found_in_rpe", "keys"] == 2
    check = p8_switch({"p3_fail_snapshots": eod, "p3_rpe_fails": rpe})
    assert check.status == "warn"
    assert "50.0% of RPE Age-1 keys" in check.summary


def test_profile_markdown_lists_every_check(raw_frames, settings):
    md = to_markdown(run_profile(settings, raw_frames))
    for check_id in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"):
        assert f"| {check_id} |" in md
