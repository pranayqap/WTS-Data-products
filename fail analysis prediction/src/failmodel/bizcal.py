"""Business-day calendar built from ``ControlDB.dbo.DateMaster`` (one row per business day)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def to_day(values) -> pd.Series | pd.Timestamp:
    """Normalize dates to midnight ``datetime64[ns]`` so frames from different sources join cleanly."""
    if isinstance(values, (pd.Series, pd.Index)):
        out = pd.to_datetime(values, errors="coerce")
        out = out.dt.normalize() if isinstance(out, pd.Series) else out.normalize()
        return out.astype("datetime64[ns]")
    return pd.Timestamp(values).normalize()


class BusinessCalendar:
    def __init__(self, business_days):
        days = pd.DatetimeIndex(to_day(pd.Series(list(business_days)))).dropna().unique().sort_values()
        if len(days) == 0:
            raise ValueError("calendar has no business days")
        self.days = days.astype("datetime64[ns]")
        self._values = self.days.values

    @classmethod
    def from_date_master(cls, frame: pd.DataFrame) -> "BusinessCalendar":
        return cls(frame["ActualDate"])

    @classmethod
    def weekdays(cls, start, end, holidays=()) -> "BusinessCalendar":
        days = pd.bdate_range(start, end)
        hol = set(pd.DatetimeIndex(pd.to_datetime(list(holidays))).normalize()) if holidays else set()
        return cls([d for d in days if d not in hol])

    def __len__(self) -> int:
        return len(self.days)

    def index_on_or_before(self, dates) -> np.ndarray:
        """Position of the latest business day on or before each date (-1 if before the calendar)."""
        arr = np.asarray(to_day(pd.Series(dates)), dtype="datetime64[ns]")
        return np.searchsorted(self._values, arr, side="right") - 1

    def is_business_day(self, dates) -> np.ndarray:
        arr = np.asarray(to_day(pd.Series(dates)), dtype="datetime64[ns]")
        pos = np.searchsorted(self._values, arr, side="left")
        ok = pos < len(self._values)
        out = np.zeros(len(arr), dtype=bool)
        out[ok] = self._values[pos[ok]] == arr[ok]
        return out

    def add(self, dates, n) -> pd.Series:
        """Shift each date by ``n`` business days. Non-business dates roll back first. NaT outside range."""
        s = pd.Series(dates)
        idx = self.index_on_or_before(s) + np.asarray(n)
        valid = (idx >= 0) & (idx < len(self._values)) & s.notna().to_numpy()
        out = np.full(len(s), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
        out[valid] = self._values[idx[valid]]
        return pd.Series(out, index=s.index)

    def bd_diff(self, start, end) -> pd.Series:
        """Business days from ``start`` to ``end`` (positive when end is later)."""
        end_s = pd.Series(end)
        a = pd.Series(start).reset_index(drop=True)
        b = end_s.reset_index(drop=True)
        diff = (self.index_on_or_before(b) - self.index_on_or_before(a)).astype("float")
        diff[(a.isna() | b.isna()).to_numpy()] = np.nan
        return pd.Series(diff, index=end_s.index)

    def next_business_day(self, d) -> pd.Timestamp:
        return self.add(pd.Series([d]), 1).iloc[0]

    def prev_business_day(self, d) -> pd.Timestamp:
        d = to_day(d)
        pos = self.index_on_or_before(pd.Series([d]))[0]
        if self._values[pos] == np.datetime64(d):
            pos -= 1
        return pd.Timestamp(self._values[pos])

    def between(self, start, end) -> pd.DatetimeIndex:
        return self.days[(self.days >= to_day(start)) & (self.days <= to_day(end))]

    def month_end_flags(self) -> pd.DataFrame:
        frame = pd.DataFrame({"day": self.days})
        period = frame["day"].dt.to_period("M")
        frame["is_month_end"] = period.ne(period.shift(-1)).astype("int8")
        quarter = frame["day"].dt.to_period("Q")
        frame["is_quarter_end"] = quarter.ne(quarter.shift(-1)).astype("int8")
        gap = frame["day"].diff().dt.days
        weekday_gap = frame["day"].dt.dayofweek.eq(0).map({True: 3, False: 1})
        frame["is_post_holiday"] = (gap > weekday_gap).astype("int8")
        frame.loc[0, "is_post_holiday"] = 0
        return frame


def as_date(d) -> date:
    return pd.Timestamp(d).date()
