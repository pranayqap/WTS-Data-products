"""Source registry: extract procedure, as-of key and availability lag for every source.

Availability lag is in business days: a row scored on D may only use source rows whose as-of date is
on or before D minus the lag. Lags marked provisional are conservative defaults until profiling
checks P2 to P4 confirm when each source lands relative to 22:30 ET on D.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    procedure: str
    asof_col: str
    availability_lag_bd: int
    chunk_days: int
    books: tuple[str, ...]
    role: str
    lag_check: str = ""
    conditional: bool = False

    @property
    def lag_is_provisional(self) -> bool:
        return bool(self.lag_check)


_P = "usp_FailModel_Extract_"

SOURCES: dict[str, Source] = {
    s.id: s
    for s in [
        Source("beta_trades", _P + "BetaTrades", "TradeDate", 0, 1, ("B",), "trade universe"),
        Source("beta_fail_snapshots", _P + "BetaFailSnapshots", "SnapShotDate", 1, 7, ("B",), "label, fail pressure", "P2"),
        Source("beta_security", _P + "BetaSecurity", "SnapShotDate", 1, 1, ("B", "P"), "security reference", "P4"),
        Source("beta_segregation", _P + "BetaSegregation", "SnapShotDate", 1, 3, ("B",), "box inventory", "P4"),
        Source("beta_close_history", _P + "BetaCloseHistory", "TradeDate", 1, 7, ("B", "P"), "liquidity", "P4"),
        Source("beta_id_confirms", _P + "BetaIDConfirms", "SnapshotDate", 1, 7, ("B",), "affirmation", "P4"),
        Source("beta_threshold_history", _P + "BetaThresholdHistory", "EffectiveDate", 0, 36500, ("B", "P"), "Reg SHO threshold"),
        Source("p3_trades", _P + "P3Trades", "TRADE_FULL_DATE", 0, 1, ("P",), "trade universe"),
        Source("p3_fail_snapshots", _P + "P3FailSnapshots", "SnapShotDate", 0, 7, ("P",), "label, fail pressure", "P2"),
        Source("p3_rpe_fails", _P + "P3RpeFails", "SnapShotDate", 1, 7, ("P",), "report-side fails (P8 reconciliation)", "P4"),
        Source("p3_eod_prices", _P + "P3EodPrices", "SnapShotDate", 1, 3, ("B", "P"), "point-in-time price", "P4"),
        Source("cns_p3", _P + "CnsP3", "PositionDate", 1, 7, ("P",), "CNS positions", "P6"),
        Source("cns_beta", _P + "CnsBeta", "PositionDate", 1, 7, ("B",), "CNS positions", "P6"),
        Source("smartloan_avail", _P + "SmartLoanAvail", "FileDate", 1, 3, ("B", "P"), "lendable supply", "P4"),
        Source("loanet_positions", _P + "LoanetPositions", "TradeDate", 1, 7, ("B", "P"), "street tightness", "P4"),
        Source("borrow_rates", _P + "BorrowRates", "FileDate", 1, 7, ("B", "P"), "borrow cost", "P4"),
        Source("reg204", _P + "Reg204", "ReportDate", 1, 31, ("B", "P"), "Reg SHO 204", "P4"),
        Source("business_calendar", _P + "BusinessCalendar", "ActualDate", 0, 36500, ("B", "P"), "calendar"),
        Source("stock_loan_borrow", _P + "StockLoanBorrow", "SettleDate", 1, 7, ("B",), "borrow position", "P3", True),
        Source("borrow_for_needs", _P + "BorrowForNeeds", "SettleDate", 1, 7, ("B",), "borrow needs", "P3", True),
        Source("locates", _P + "Locates", "TradeDate", 1, 31, ("B", "P"), "locate demand", "P4", True),
    ]
}


def lag(source_id: str) -> int:
    return SOURCES[source_id].availability_lag_bd


def active_sources(include_conditional: bool = False) -> list[Source]:
    return [s for s in SOURCES.values() if include_conditional or not s.conditional]
