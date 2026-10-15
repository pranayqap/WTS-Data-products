"""Map Beta and P3 raw extracts (native column names) to one canonical trade and one canonical fail schema.

Mapping rules follow ``GetBetaFailAnalysisData_Report`` and ``GetP3FailAnalysisData_Report`` where those
procedures define them (direction, counterparty type, product groups, population filters).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .bizcal import to_day
from .config import Settings

TRADE_COLUMNS = [
    "trade_key", "fallback_key", "book", "trade_date", "settle_date", "entry_date", "direction",
    "account", "account_type", "cusip9", "security_number", "security_type", "product_group",
    "quantity", "price", "notional", "counterparty_type", "counterparty_id", "dvp_flag",
    "standing_instruction", "capacity", "exec_hour", "is_asof", "non_usd", "cancelled",
    "control_number", "unique_ref",
]

FAIL_COLUMNS = [
    "fail_key", "trade_key", "fallback_key", "book", "snapshot_date", "direction", "account",
    "account_type", "cusip9", "security_number", "security_type", "product_group", "quantity",
    "original_quantity", "amount", "settle_date", "trade_date", "entry_date", "control_number",
    "counterparty_id", "dk_count", "reason_code", "fail_who", "fail_condition", "stock_loan_rate",
    "age_cal", "in_report_population", "raw_buy_sell", "record_type", "update_time",
]

BETA_PRODUCT = {"C": "EQ", "B": "CORP", "M": "MUNI", "T": "FED"}
P3_PRODUCT = {"A": "EQ", "B": "EQ", "J": "CORP", "S": "CORP", "L": "MUNI",
              "M": "FED", "P": "FED", "Q": "FED", "R": "FED", "O": "FED"}
P3_PER_100_TYPES = {"J", "L", "N", "P", "Q", "R", "S", "K", "M", "O"}


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df.columns else pd.Series(pd.NA, index=df.index, dtype="object")


def _str(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _int_str(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round().astype("Int64").astype("string")


def _ymd(series: pd.Series) -> pd.Series:
    return series.dt.strftime("%Y%m%d").astype("string")


def _qty_str(series: pd.Series) -> pd.Series:
    return series.round(5).map(lambda v: "" if pd.isna(v) else f"{v:.5f}").astype("string")


def _cusip9(series: pd.Series) -> pd.Series:
    return _str(series).str.upper().str[:9]


normalize_cusip9 = _cusip9


def make_key(prefix: str, parts: list[pd.Series]) -> pd.Series:
    out = pd.Series(prefix, index=parts[0].index, dtype="string")
    for part in parts:
        out = out + "|" + part.astype("string").fillna("")
    return out


def _exec_hour(series: pd.Series) -> pd.Series:
    parts = _str(series).str.extract(r"^(\d{1,2})[:.]?(\d{2})")
    return _num(parts[0]) + _num(parts[1]) / 60.0


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


def canonical_beta_trades(raw: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    if raw.empty:
        return _empty(TRADE_COLUMNS)
    trade_date = to_day(raw["TradeDate"])
    entry = to_day(pd.to_datetime(_str(_col(raw, "TradeTimestamp")).str[:10], format="%Y-%m-%d", errors="coerce"))
    entry = entry.fillna(trade_date)
    control = pd.to_numeric(raw["ControlNumber"], errors="coerce").astype("Int64")
    account = _int_str(raw["AccountNumber"])
    secnum = _int_str(raw["SecurityNumber"])
    direction = _str(raw["PurchaseOrSale"]).str.upper().map(settings.beta_direction_map)
    quantity = _num(raw["TradeQuantity"]).abs()
    price = _num(raw["TradePrice"])
    notional = _num(raw["NetAmount"]).abs()
    notional = notional.where(notional > 0, quantity * price)
    acct_num = pd.to_numeric(raw["AccountNumber"], errors="coerce")
    broker = _num(_col(raw, "ContraBrokerNumber"))
    dealer = _num(_col(raw, "ContraDealerNumber"))
    contra = broker.where(broker > 0, dealer)
    standing = _str(_col(raw, "StandingInstructionCode"))
    sec_type = _str(_col(raw, "SecurityType")).str.upper()
    foreign = _num(_col(raw, "foreign_curr"))

    out = pd.DataFrame(
        {
            "trade_key": make_key("B", [control.astype("string"), _ymd(trade_date), account, secnum]),
            "fallback_key": make_key("BF", [_ymd(trade_date), account, secnum, direction, _qty_str(quantity)]),
            "book": "B",
            "trade_date": trade_date,
            "settle_date": to_day(raw["SettleDate"]),
            "entry_date": entry,
            "direction": direction,
            "account": account,
            "account_type": _int_str(_col(raw, "AccountType")),
            "cusip9": _cusip9(raw["CUSIP"]),
            "security_number": secnum,
            "security_type": sec_type,
            "product_group": sec_type.map(BETA_PRODUCT).fillna("OTHER"),
            "quantity": quantity,
            "price": price,
            "notional": notional,
            "counterparty_type": np.where(acct_num.between(100000, 999999), "Broker", "Customer"),
            "counterparty_id": ("B:" + _int_str(contra)).where(contra > 0),
            "dvp_flag": standing.isin(settings.beta_dvp_codes).astype("int8"),
            "standing_instruction": standing,
            "capacity": _str(_col(raw, "FirmCapacityCode")),
            "exec_hour": _exec_hour(_col(raw, "ExecutionTime")),
            "is_asof": (entry > trade_date).astype("int8"),
            "non_usd": (foreign.fillna(0) != 0).astype("int8"),
            "cancelled": _num(raw["Cancelled"]).fillna(0).astype(bool).astype("int8"),
            "control_number": control,
            "unique_ref": _str(_col(raw, "BETAUniqueKey")),
        }
    )
    return out[TRADE_COLUMNS]


P3_KEY_COLS = ["TRADE_FULL_DATE", "ACCT_NBR", "CCURRENCY", "ACCT_TYPE", "MARKET", "TRAN_NBR", "FIRM_NBR"]


def p3_trade_key(raw: pd.DataFrame) -> pd.Series:
    parts = [_ymd(to_day(raw["TRADE_FULL_DATE"]))] + [_str(raw[c]) for c in P3_KEY_COLS[1:]]
    return make_key("P", parts)


def dedup_p3_trades(raw: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule != "latest_seq_nbr":
        raise ValueError(f"unknown P3 dedup rule {rule!r}")
    frame = raw.assign(
        _key=p3_trade_key(raw),
        _seq=pd.to_numeric(_col(raw, "SEQ_NBR"), errors="coerce").fillna(0),
        _side=_str(_col(raw, "SIDE_CODE")).fillna(""),
        _side_seq=pd.to_numeric(_col(raw, "SIDE_SEQ_NBR"), errors="coerce").fillna(0),
    )
    frame = frame.sort_values(["_key", "_seq", "_side", "_side_seq"], ascending=[True, False, True, True])
    return frame.drop_duplicates("_key", keep="first").drop(columns=["_seq", "_side", "_side_seq"])


def canonical_p3_trades(raw: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    if raw.empty:
        return _empty(TRADE_COLUMNS)
    df = dedup_p3_trades(raw, settings.p3_dedup_rule)
    trade_date = to_day(df["TRADE_FULL_DATE"])
    entry = to_day(_col(df, "ENTRY_DATE")).fillna(trade_date)
    account = _str(df["ACCT_NBR"])
    sec_type = _str(_col(df, "SEC_TYPE")).str.upper()
    quantity = _num(df["QUANTITY"]).abs()
    price = _num(df["PRICE"])
    scale = np.where(sec_type.isin(P3_PER_100_TYPES).fillna(False), 0.01, 1.0)
    notional = _num(df["NET_AMOUNT"]).abs()
    notional = notional.where(notional > 0, quantity * price * scale)
    del_code = _str(_col(df, "DEL_CODE"))
    cxl = _str(_col(df, "CXL_COR_REASON")).fillna("")
    unmatched = _str(_col(df, "UNMATCHED_CANCEL")).fillna("").str.upper()
    contra = _str(_col(df, "CONTRA_ACCT_NBR_1"))
    cp_type = np.select(
        [account.str.startswith("94").fillna(False), account.str[:2].isin(["89", "91"]).fillna(False)],
        ["Broker", "Suspense"],
        default="Customer",
    )

    out = pd.DataFrame(
        {
            "trade_key": df["_key"].astype("string"),
            "fallback_key": pd.Series(pd.NA, index=df.index, dtype="string"),
            "book": "P",
            "trade_date": trade_date,
            "settle_date": to_day(df["SETTLE_FULL_DATE"]),
            "entry_date": entry,
            "direction": _str(df["BUY_SELL"]).str.upper().map(settings.p3_direction_map),
            "account": account,
            "account_type": _str(df["ACCT_TYPE"]),
            "cusip9": _cusip9(df["CUSIP_NUMBER"]),
            "security_number": pd.Series(pd.NA, index=df.index, dtype="string"),
            "security_type": sec_type,
            "product_group": sec_type.map(P3_PRODUCT).fillna("OTHER"),
            "quantity": quantity,
            "price": price,
            "notional": notional,
            "counterparty_type": cp_type,
            "counterparty_id": ("P:" + contra).where(contra.fillna("") != ""),
            "dvp_flag": del_code.isin(settings.p3_dvp_codes).astype("int8"),
            "standing_instruction": del_code,
            "capacity": _str(_col(df, "CAPACITY_CODE")),
            "exec_hour": _num(_col(df, "EXEC_HH")) + _num(_col(df, "EXEC_MM")).fillna(0) / 60.0,
            "is_asof": (entry > trade_date).astype("int8"),
            "non_usd": (_str(df["CCURRENCY"]).fillna("USD") != "USD").astype("int8"),
            "cancelled": ((cxl != "") | (unmatched == "Y")).astype("int8"),
            "control_number": pd.Series(pd.NA, index=df.index, dtype="Int64"),
            "unique_ref": df["_key"].astype("string"),
        }
    )
    return out[TRADE_COLUMNS].reset_index(drop=True)


def canonical_beta_fails(raw: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    if raw.empty:
        return _empty(FAIL_COLUMNS)
    snapshot = to_day(raw["SnapShotDate"])
    trade_date = to_day(raw["TradeDate"])
    settle = to_day(raw["SettleDate"])
    control = pd.to_numeric(raw["ControlNumber"], errors="coerce").fillna(0).astype("Int64")
    account = _int_str(raw["AccountNumber"])
    secnum = _int_str(raw["SecurityNumber"])
    rd = pd.to_numeric(raw["ReceiveDeliverCode"], errors="coerce")
    direction = rd.map({0: "D", 1: "R"}).astype("string")
    quantity = _num(raw["Quantity"]).abs()
    sec_type = _str(_col(raw, "SecurityTypeCode")).str.upper()
    acct_type = pd.to_numeric(_col(raw, "AccountType"), errors="coerce")
    nscc = _num(_col(raw, "NSCCNumber"))
    has_control = control != 0
    zero_key = make_key("BZ", [_ymd(settle.fillna(trade_date)), account, rd.astype("Int64").astype("string"), secnum])
    unique_id = "BU|" + pd.to_numeric(_col(raw, "FailUniqueId"), errors="coerce").astype("Int64").astype("string")
    age_base = settle.fillna(trade_date)

    out = pd.DataFrame(
        {
            "fail_key": unique_id.where(has_control, zero_key),
            "trade_key": make_key("B", [control.astype("string"), _ymd(trade_date), account, secnum]).where(has_control),
            "fallback_key": make_key("BF", [_ymd(trade_date), account, secnum, direction, _qty_str(quantity)]),
            "book": "B",
            "snapshot_date": snapshot,
            "direction": direction,
            "account": account,
            "account_type": acct_type.astype("Int64").astype("string"),
            "cusip9": _cusip9(raw["CUSIP"]),
            "security_number": secnum,
            "security_type": sec_type,
            "product_group": sec_type.map(BETA_PRODUCT).fillna("OTHER"),
            "quantity": quantity,
            "original_quantity": quantity,
            "amount": _num(_col(raw, "Amount")).abs(),
            "settle_date": settle,
            "trade_date": trade_date,
            "entry_date": pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]"),
            "control_number": control,
            "counterparty_id": ("B:" + _int_str(nscc)).where(nscc > 0),
            "dk_count": _num(_col(raw, "DKCount")),
            "reason_code": _str(_col(raw, "DTCReasonCode")),
            "fail_who": _str(_col(raw, "FailWho")),
            "fail_condition": _str(_col(raw, "FailConditionCode")),
            "stock_loan_rate": _num(_col(raw, "StockLoanRate")),
            "age_cal": (snapshot - age_base).dt.days + 1,
            "in_report_population": (
                (acct_type.fillna(-1) != 8)
                & (sec_type.fillna("") != "P")
                & (settle.isna() | (settle <= snapshot))
            ).astype("int8"),
            "raw_buy_sell": pd.Series(pd.NA, index=raw.index, dtype="string"),
            "record_type": pd.Series(pd.NA, index=raw.index, dtype="string"),
            "update_time": pd.to_datetime(_col(raw, "UpdateDateTime"), errors="coerce"),
        }
    )
    return out[FAIL_COLUMNS]


def canonical_p3_fails(raw: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    if raw.empty:
        return _empty(FAIL_COLUMNS)
    snapshot = to_day(raw["SnapShotDate"])
    settle = to_day(raw["SETTLE_DATE"])
    trade_date = to_day(raw["TRADE_FULL_DATE"])
    qty = _num(raw["QUANTITY"])
    net = _num(_col(raw, "NET_AMOUNT"))
    direction = pd.Series(
        np.select([(qty < 0) | (net < 0), (qty > 0) | (net > 0)], ["R", "D"], default=""), index=raw.index
    ).replace("", pd.NA).astype("string")
    trade_key = p3_trade_key(raw)
    acct_type = _str(raw["ACCT_TYPE"])
    contra = _str(_col(raw, "CONTRA_ACCT_NBR_1"))
    age_base = settle.fillna(trade_date)

    out = pd.DataFrame(
        {
            "fail_key": trade_key + "|" + _str(_col(raw, "RECORD_TYPE")).fillna(""),
            "trade_key": trade_key,
            "fallback_key": pd.Series(pd.NA, index=raw.index, dtype="string"),
            "book": "P",
            "snapshot_date": snapshot,
            "direction": direction,
            "account": _str(raw["ACCT_NBR"]),
            "account_type": acct_type,
            "cusip9": _cusip9(raw["CUSIP_NUMBER"]),
            "security_number": pd.Series(pd.NA, index=raw.index, dtype="string"),
            "security_type": pd.Series(pd.NA, index=raw.index, dtype="string"),
            "product_group": pd.Series(pd.NA, index=raw.index, dtype="string"),
            "quantity": qty.abs(),
            "original_quantity": _num(_col(raw, "ORIGINAL_QUANTITY")).abs(),
            "amount": net.abs(),
            "settle_date": settle,
            "trade_date": trade_date,
            "entry_date": to_day(_col(raw, "ENTRY_DATE")),
            "control_number": pd.Series(pd.NA, index=raw.index, dtype="Int64"),
            "counterparty_id": ("P:" + contra).where(contra.fillna("") != ""),
            "dk_count": pd.Series(np.nan, index=raw.index),
            "reason_code": _str(_col(raw, "FAIL_REASON")),
            "fail_who": pd.Series(pd.NA, index=raw.index, dtype="string"),
            "fail_condition": _str(_col(raw, "DTC_DO_REASON_CODE")),
            "stock_loan_rate": pd.Series(np.nan, index=raw.index),
            "age_cal": (snapshot - age_base).dt.days + 1,
            "in_report_population": (
                ~acct_type.isin(["2", "3"]).fillna(False) & (settle.isna() | (settle <= snapshot))
            ).astype("int8"),
            "raw_buy_sell": _str(_col(raw, "BUY_SELL")),
            "record_type": _str(_col(raw, "RECORD_TYPE")),
            "update_time": pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]"),
        }
    )
    return out[FAIL_COLUMNS]


def canonical_trades(beta_raw: pd.DataFrame, p3_raw: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    parts = [canonical_beta_trades(beta_raw, settings), canonical_p3_trades(p3_raw, settings)]
    parts = [p for p in parts if len(p)]
    if not parts:
        return _empty(TRADE_COLUMNS)
    return pd.concat(parts, ignore_index=True)


def canonical_fails(beta_raw: pd.DataFrame, p3_raw: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    parts = [canonical_beta_fails(beta_raw, settings), canonical_p3_fails(p3_raw, settings)]
    parts = [p for p in parts if len(p)]
    if not parts:
        return _empty(FAIL_COLUMNS)
    return pd.concat(parts, ignore_index=True)
