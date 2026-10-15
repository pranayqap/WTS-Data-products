import pandas as pd
import pytest

from failmodel.bizcal import BusinessCalendar
from failmodel.canonical import canonical_fails, canonical_trades
from failmodel.labels import base_rates, build_labels, exclusion_counts, reconcile
from failmodel.profiling import calendar_from

S = "2024-06-05"
D = "2024-06-04"


def _beta_trade(ctrl, side, qty, settle=S, trade=D, entry=None, cancelled=0, acct=200001, acct_type=1, sec=5001):
    return {
        "TradeDate": trade, "TradeTimestamp": f"{entry or trade} 10:15:00", "ControlNumber": ctrl,
        "AccountNumber": acct, "SecurityNumber": sec, "PurchaseOrSale": side, "TradeQuantity": qty,
        "TradePrice": 10.0, "NetAmount": qty * 10.0, "ContraBrokerNumber": 77, "ContraDealerNumber": 0,
        "StandingInstructionCode": "1", "SecurityType": "C", "foreign_curr": 0, "Cancelled": cancelled,
        "SettleDate": settle, "AccountType": acct_type, "CUSIP": "123456789", "FirmCapacityCode": "A",
        "ExecutionTime": "10:15", "BETAUniqueKey": f"U{ctrl}",
    }


def _beta_fail(snap, ctrl, rd, qty, trade=D, settle=S, acct=200001, acct_type=1, sec=5001, uid=None):
    return {
        "SnapShotDate": snap, "TradeDate": trade, "SettleDate": settle, "ControlNumber": ctrl,
        "AccountNumber": acct, "SecurityNumber": sec, "ReceiveDeliverCode": rd, "Quantity": qty,
        "SecurityTypeCode": "C", "AccountType": acct_type, "NSCCNumber": 77, "FailUniqueId": uid or ctrl,
        "CUSIP": "123456789",
        "Amount": qty * 10.0, "DKCount": 0, "UpdateDateTime": f"{snap} 23:30:00",
    }


def _p3_key(tran):
    return {"TRADE_FULL_DATE": D, "ACCT_NBR": "12345678", "CCURRENCY": "USD", "ACCT_TYPE": "1", "MARKET": "1",
            "TRAN_NBR": tran, "FIRM_NBR": "01"}


def _p3_trade(tran, side, qty, seq=1):
    return {
        **_p3_key(tran), "SEQ_NBR": seq, "SIDE_CODE": "", "SIDE_SEQ_NBR": 0, "ENTRY_DATE": D, "SEC_TYPE": "A",
        "QUANTITY": qty, "PRICE": 20.0, "NET_AMOUNT": qty * 20.0, "DEL_CODE": "", "CXL_COR_REASON": "",
        "UNMATCHED_CANCEL": "", "CONTRA_ACCT_NBR_1": "9400001", "SETTLE_FULL_DATE": S, "BUY_SELL": side,
        "CUSIP_NUMBER": "987654321", "CAPACITY_CODE": "A", "EXEC_HH": 11, "EXEC_MM": 30,
    }


def _p3_fail(snap, tran, qty, record_type="F", settle=S):
    return {
        **_p3_key(tran), "SnapShotDate": snap, "SETTLE_DATE": settle, "QUANTITY": qty, "NET_AMOUNT": qty * 20.0,
        "RECORD_TYPE": record_type, "CUSIP_NUMBER": "987654321", "ORIGINAL_QUANTITY": abs(qty), "ENTRY_DATE": D,
        "CONTRA_ACCT_NBR_1": "9400001", "FAIL_REASON": "", "DTC_DO_REASON_CODE": "", "BUY_SELL": "B" if qty > 0 else "S",
    }


@pytest.fixture()
def scenario(settings):
    beta_trades = pd.DataFrame([
        _beta_trade(101, "P", 100),                      # exact match
        _beta_trade(102, "S", 200),                      # no fail
        _beta_trade(103, "P", 300),                      # fallback match (fail row has ControlNumber 0)
        _beta_trade(104, "P", 400, settle="2024-06-06"), # label snapshot missing
        _beta_trade(105, "P", 500, cancelled=1),
        _beta_trade(106, "P", 600, entry=S),             # entered on S
        _beta_trade(107, "P", 700, acct_type=8),         # excluded account type, but it fails
        _beta_trade(108, "P", 1000),                     # partial fail
    ])
    beta_fails = pd.DataFrame([
        _beta_fail(S, 101, 0, 100),
        _beta_fail(S, 0, 0, 300, uid=9001),
        _beta_fail(S, 107, 0, 700, acct_type=8),       # outside the report population
        _beta_fail(S, 106, 0, 600),                    # fails, but was entered on S
        _beta_fail(S, 108, 0, 400),
        _beta_fail(S, 0, 1, 50, acct=999999, sec=7777, uid=9002),  # non-trade, zero control
        _beta_fail("2024-06-07", 101, 0, 100),                     # T1 still open; 06-06 has no snapshot
    ])
    p3_trades = pd.DataFrame([
        _p3_trade("A1", "B", 100, seq=1),
        _p3_trade("A1", "B", 150, seq=2),  # later correction wins
        _p3_trade("A2", "S", 80),
    ])
    p3_fails = pd.DataFrame([
        _p3_fail(S, "A1", 150),
        _p3_fail(S, "Z9", -60, record_type="T"),  # non-trade record type
        _p3_fail(S, "Z8", 40),                    # trade record type, trade not extracted
        _p3_fail("2024-06-06", "Z8", 40, settle=S),
        _p3_fail("2024-06-07", "Z8", 40, settle=S),
    ])
    cal = BusinessCalendar.weekdays("2024-05-20", "2024-06-28")
    trades = canonical_trades(beta_trades, p3_trades, settings)
    fails = canonical_fails(beta_fails, p3_fails, settings)
    window = (pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-14"))
    result = build_labels(trades, fails, cal, settings, label_window=window)
    return result, cal


def _label(result, key):
    row = result.labels.set_index("trade_key").loc[key]
    return row


def test_exact_fallback_and_missing_snapshot(scenario):
    result, _ = scenario
    t1 = _label(result, "B|101|20240604|200001|5001")
    assert t1["failed_on_settle"] == 1 and t1["match_method"] == "exact"
    assert t1["direction"] == "D"
    t2 = _label(result, "B|102|20240604|200001|5001")
    assert t2["failed_on_settle"] == 0 and t2["direction"] == "R"
    t3 = _label(result, "B|103|20240604|200001|5001")
    assert t3["failed_on_settle"] == 1 and t3["match_method"] == "fallback"
    t4 = _label(result, "B|104|20240604|200001|5001")
    assert pd.isna(t4["failed_on_settle"])


def test_partial_fail_fraction(scenario):
    result, _ = scenario
    t8 = _label(result, "B|108|20240604|200001|5001")
    assert t8["failed_on_settle"] == 1
    assert t8["fail_qty_fraction"] == pytest.approx(0.4)
    assert t8["fail_settle_date_differs"] == 0


def test_exclusions(scenario):
    result, _ = scenario
    u = result.universe.set_index("trade_key")
    assert u.loc["B|105|20240604|200001|5001", "exclusion_reason"] == "cancelled"
    assert u.loc["B|106|20240604|200001|5001", "exclusion_reason"] == "entered_after_scoring_date"
    assert u.loc["B|107|20240604|200001|5001", "exclusion_reason"] == "account_type_excluded"
    assert "B|105|20240604|200001|5001" not in set(result.labels["trade_key"])
    counts = exclusion_counts(result.universe).set_index(["book", "reason"])["trades"]
    assert counts[("B", "in_population")] == 5


def test_p3_dedup_and_direction(scenario):
    result, _ = scenario
    p1 = _label(result, "P|20240604|12345678|USD|1|1|A1|01")
    assert p1["quantity"] == 150 and p1["direction"] == "D" and p1["failed_on_settle"] == 1
    assert (result.labels["trade_key"] == "P|20240604|12345678|USD|1|1|A1|01").sum() == 1
    p2 = _label(result, "P|20240604|12345678|USD|1|1|A2|01")
    assert p2["failed_on_settle"] == 0 and p2["direction"] == "R"


def test_reconciliation_categories(scenario, settings):
    result, cal = scenario
    recon = reconcile(result, cal, settings)
    summary = recon["summary"].set_index("book")
    b, p = summary.loc["B"], summary.loc["P"]
    assert b["age1_fails"] == 5
    assert b["matched_in_population"] == 3
    assert b["matched_excluded_trade"] == 1
    assert b["unmatched_zero_control"] == 1
    excluded = recon["excluded_reasons"].set_index(["book", "exclusion_reason"])["fails"]
    assert excluded[("B", "entered_after_scoring_date")] == 1
    assert b["match_rate_trade_originated"] == 1.0
    assert p["matched_in_population"] == 1
    assert p["unmatched_non_trade_record"] == 1
    assert p["unmatched_trade_missing"] == 1
    assert p["match_rate_trade_originated"] == pytest.approx(0.5)


def test_unknown_labels_counted_not_negative(scenario):
    result, _ = scenario
    rates = base_rates(result.labels).set_index(["book", "direction"])
    assert rates.loc[("B", "D"), "unknown_label_rows"] == 1
    assert rates.loc[("B", "D"), "trades"] == 3
    assert rates.loc[("B", "D"), "fails"] == 3


def test_synthetic_reconciliation_meets_exit_criterion(raw_frames, settings):
    cal = calendar_from(raw_frames)
    trades = canonical_trades(raw_frames["beta_trades"], raw_frames["p3_trades"], settings)
    fails = canonical_fails(raw_frames["beta_fail_snapshots"], raw_frames["p3_fail_snapshots"], settings)
    result = build_labels(trades, fails, cal, settings)
    recon = reconcile(result, cal, settings)
    summary = recon["summary"].set_index("book")
    assert (summary["match_rate_trade_originated"] >= 0.9).all()
    daily = recon["daily"]
    matched = daily["matched_in_population"].sum()
    assert abs(daily["positive_labels"].sum() - matched) <= 0.01 * matched
    labels = result.labels
    assert labels["failed_on_settle"].isna().sum() > 0
    assert labels.groupby("trade_key").size().max() == 1
