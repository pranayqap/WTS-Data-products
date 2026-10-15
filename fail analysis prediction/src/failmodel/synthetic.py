"""Synthetic WSI extracts in the native column layout of the extract procedures.

Used to exercise the full pipeline (profiling, labels, features, models, scoring, persistence) before real
extracts are available. The generating process plants known structure: a latent per-CUSIP borrow
"hardness" that drives spreads, chills, borrow cost, lendable supply, box deficits and fails; counterparty
and account quality; DVP and affirmation effects; late execution; and persistent fails. Nothing here is
calibrated to real WSI rates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .bizcal import BusinessCalendar
from .extract import write_raw

HOLIDAYS = [
    "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
    "2024-11-28", "2024-12-25", "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
]

BETA_SEC = {"EQ": "C", "CORP": "B", "MUNI": "M", "FED": "T"}
P3_SEC = {"EQ": "A", "CORP": "J", "MUNI": "L", "FED": "M"}


@dataclass(frozen=True)
class SyntheticSpec:
    start: str = "2024-01-02"
    end: str = "2025-06-30"
    n_cusips: int = 400
    n_contras: int = 60
    n_accounts: int = 500
    trades_per_day: int = 150
    seed: int = 11
    missing_beta_snapshots: int = 2
    missing_p3_snapshots: int = 1
    label_start: str = "2024-05-28"


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _fmt_ts(days: pd.Series, hours: np.ndarray) -> pd.Series:
    hh = np.clip(np.floor(hours), 0, 23).astype(int)
    mm = np.clip(np.round((hours - np.floor(hours)) * 60), 0, 59).astype(int)
    return days.dt.strftime("%Y-%m-%d") + "-" + pd.Series(hh, index=days.index).map("{:02d}".format) + "." + \
        pd.Series(mm, index=days.index).map("{:02d}".format) + ".00.000000"


class _Generator:
    def __init__(self, spec: SyntheticSpec):
        self.spec = spec
        self.rng = np.random.default_rng(spec.seed)
        self.cal = BusinessCalendar.weekdays(spec.start, spec.end, HOLIDAYS)
        self.days = pd.Series(self.cal.days)
        self.T = len(self.days)
        self.C = spec.n_cusips

    def securities(self):
        rng, C = self.rng, self.C
        product = rng.choice(["EQ", "CORP", "MUNI", "FED"], C, p=[0.7, 0.15, 0.1, 0.05])
        self.sec = pd.DataFrame(
            {
                "cusip9": [f"9{i:05d}AB{i % 10}" for i in range(C)],
                "security_number": 100000 + np.arange(C),
                "product": product,
                "adv": np.where(product == "EQ", np.exp(rng.normal(11.5, 1.4, C)), np.exp(rng.normal(9.5, 1.0, C))),
                "p0": np.where(product == "EQ", np.exp(rng.normal(3.5, 0.8, C)), rng.normal(100, 4, C)),
                "popularity": rng.pareto(1.2, C) + 0.2,
            }
        )
        self.sec["symbol"] = ["SY" + f"{i:04d}" for i in range(C)]
        self.pop_w = (self.sec["popularity"] / self.sec["popularity"].sum()).to_numpy()
        mu = rng.normal(0, 1, C)
        x = np.zeros((self.T, C))
        x[0] = rng.normal(0, 0.5, C)
        for t in range(1, self.T):
            x[t] = 0.95 * x[t - 1] + rng.normal(0, 0.2, C)
        self.H = 0.8 * mu + 0.8 * x
        is_eq = (product == "EQ")[None, :]
        vol = np.where(is_eq, 0.014, 0.003) * np.exp(0.25 * self.H)
        logp = np.log(self.sec["p0"].to_numpy())[None, :] + np.cumsum(rng.normal(0, vol), axis=0)
        self.P = np.exp(logp)
        self.V = self.sec["adv"].to_numpy()[None, :] * np.exp(rng.normal(0, 0.35, (self.T, C)) - 0.15 * self.H)
        self.REQ = self.sec["adv"].to_numpy()[None, :] * 0.4 * np.exp(rng.normal(0, 0.15, (self.T, C)))
        self.AVAIL = self.REQ * np.exp(0.2 - 0.45 * self.H + rng.normal(0, 0.25, (self.T, C)))
        self.DEFICIT = self.REQ > self.AVAIL

    def parties(self):
        rng, n = self.rng, self.spec.n_accounts
        self.contra_q = rng.normal(0, 0.8, self.spec.n_contras)
        self.beta_contras = 100 + rng.choice(900, self.spec.n_contras, replace=False)
        self.p3_contras = np.array([f"94{v:06d}" for v in rng.choice(999999, self.spec.n_contras, replace=False)])
        accts = {}
        for book in ("B", "P"):
            broker = rng.random(n) < 0.3
            if book == "B":
                number = np.where(broker, 100000 + rng.choice(899999, n, replace=False),
                                  10000 + rng.choice(89999, n, replace=False)).astype(str)
                acct_type = np.where(rng.random(n) < 0.03, "8", np.where(rng.random(n) < 0.5, "1", "2"))
            else:
                suspense = ~broker & (rng.random(n) < 0.03)
                base = rng.choice(999999, n, replace=False)
                number = np.where(broker, [f"94{v:06d}" for v in base],
                                  np.where(suspense, [f"89{v:06d}" for v in base], [f"1{v:07d}" for v in base]))
                acct_type = np.where(rng.random(n) < 0.02, "3", np.where(rng.random(n) < 0.5, "1", "2"))
            accts[book] = pd.DataFrame(
                {
                    "number": number,
                    "acct_type": acct_type,
                    "broker": broker,
                    "dvp": ~broker & (rng.random(n) < 0.25),
                    "quality": rng.normal(0, 0.5, n),
                    "pref_contra": rng.integers(0, self.spec.n_contras, n),
                }
            )
        self.accts = accts

    def trades(self, book: str) -> pd.DataFrame:
        rng, T = self.rng, self.T
        counts = rng.poisson(self.spec.trades_per_day, T)
        t_idx = np.repeat(np.arange(T), counts)
        n = len(t_idx)
        c = rng.choice(self.C, n, p=self.pop_w)
        a = rng.integers(0, len(self.accts[book]), n)
        acct = self.accts[book].iloc[a].reset_index(drop=True)
        contra = np.where(rng.random(n) < 0.7, acct["pref_contra"].to_numpy(), rng.integers(0, self.spec.n_contras, n))
        cycle = rng.choice([1, 2, 0], n, p=[0.92, 0.05, 0.03])
        late_entry = (rng.random(n) < 0.04).astype(int)
        product = self.sec["product"].to_numpy()[c]
        adv = self.sec["adv"].to_numpy()[c]
        qty = np.where(
            product == "EQ",
            np.maximum(100, np.round(adv * np.exp(rng.normal(-6.0, 1.2, n)) / 100) * 100),
            1000 * rng.integers(5, 500, n),
        )
        price = self.P[t_idx, c] * (1 + rng.normal(0, 0.002, n))
        notional = np.where(product == "EQ", qty * price, qty * price / 100)
        exec_hour = np.where(rng.random(n) < 0.08, 16 + 2 * rng.random(n), 9.5 + 6.5 * rng.beta(2, 2, n))
        dvp = acct["dvp"].to_numpy()
        matched = rng.random(n) < 0.85
        affirmed = dvp & matched & (rng.random(n) < 0.85)
        frame = pd.DataFrame(
            {
                "book": book, "t": t_idx, "s": t_idx + cycle, "e": t_idx + late_entry, "c": c, "a": a,
                "contra": contra, "direction": np.where(rng.random(n) < 0.5, "D", "R"), "qty": qty,
                "price": price, "notional": notional, "exec_hour": exec_hour, "dvp": dvp,
                "matched": dvp & matched, "affirmed": affirmed, "cancelled": rng.random(n) < 0.02,
                "acct_quality": acct["quality"].to_numpy(), "product": product,
                "non_usd": (rng.random(n) < 0.01) & (book == "P"),
            }
        )
        frame = frame[(frame["s"] < T) & (frame["e"] < T)].reset_index(drop=True)
        frame["row"] = np.arange(len(frame))
        frame["seq_in_day"] = frame.groupby("t").cumcount()
        return frame

    def simulate_fails(self, trades: pd.DataFrame):
        rng, T, C = self.rng, self.T, self.C
        open_cnt = {d: np.zeros((T, C)) for d in "DR"}
        open_qty = {(b, d): np.zeros((T, C)) for b in "BP" for d in "DR"}
        registry, snaps = [], []
        open_df = pd.DataFrame(columns=["fid", "book", "direction", "c", "qty", "settle_i", "opened_i", "trade_row",
                                        "zero_control", "contra", "nt_acct", "nt_tran"])
        next_fid = 0
        by_settle = {s: g for s, g in trades.groupby("s")}
        for i in range(T):
            if len(open_df):
                h = self.H[max(i - 1, 0), open_df["c"].to_numpy(dtype=int)]
                q = self.contra_q[open_df["contra"].to_numpy(dtype=int)]
                dlv = open_df["direction"].to_numpy() == "D"
                p_cure = np.where(dlv, _sigmoid(0.3 - 0.6 * h - 0.5 * q), _sigmoid(0.4 - 0.7 * q))
                age = i - open_df["settle_i"].to_numpy(dtype=int)
                keep = (rng.random(len(open_df)) > p_cure) & (age <= 40)
                open_df = open_df[keep].copy()
                partial = rng.random(len(open_df)) < 0.1
                open_df.loc[partial, "qty"] = np.round(open_df.loc[partial, "qty"] * rng.uniform(0.4, 0.9, partial.sum()))
            new_parts = []
            g = by_settle.get(i)
            if g is not None and i > 0:
                d = i - 1
                c = g["c"].to_numpy()
                h = self.H[d, c]
                q = self.contra_q[g["contra"].to_numpy()]
                a = g["acct_quality"].to_numpy()
                dvp = g["dvp"].to_numpy().astype(float)
                unaff = dvp * (1 - g["affirmed"].to_numpy().astype(float))
                late = (g["exec_hour"].to_numpy() >= 15.5).astype(float)
                size = np.log1p(g["qty"].to_numpy() / self.sec["adv"].to_numpy()[c] * 1000)
                same_day = (g["e"].to_numpy() >= g["s"].to_numpy()).astype(float)
                dlv = g["direction"].to_numpy() == "D"
                pressure = np.where(dlv, open_cnt["D"][d, c] > 0, open_cnt["R"][d, c] > 0).astype(float)
                muni = (g["product"].to_numpy() == "MUNI").astype(float)
                z_d = (-5.8 + 1.0 * h + 0.6 * q + 0.4 * a + 0.5 * dvp + 0.9 * unaff + 0.4 * late + 0.25 * size
                       + 0.8 * pressure + 0.7 * self.DEFICIT[d, c] + 0.3 * muni)
                z_r = (-5.6 + 0.4 * h + 0.9 * q + 0.4 * a + 0.4 * dvp + 0.7 * unaff + 0.3 * late + 0.2 * size
                       + 0.6 * pressure)
                z = np.where(dlv, z_d, z_r) + 0.6 * same_day
                fails = (rng.random(len(g)) < _sigmoid(z)) & ~g["cancelled"].to_numpy()
                f = g[fails]
                if len(f):
                    n = len(f)
                    zero = (f["book"].to_numpy() == "B") & (rng.random(n) < 0.08)
                    new_parts.append(pd.DataFrame({
                        "fid": np.arange(next_fid, next_fid + n), "book": f["book"].to_numpy(),
                        "direction": f["direction"].to_numpy(), "c": f["c"].to_numpy(), "qty": f["qty"].to_numpy(),
                        "settle_i": i, "opened_i": i, "trade_row": f["row"].to_numpy(), "zero_control": zero,
                        "contra": f["contra"].to_numpy(), "nt_acct": -1, "nt_tran": -1,
                    }))
                    next_fid += n
            for book in "BP":
                k = rng.poisson(1.5)
                if k:
                    new_parts.append(pd.DataFrame({
                        "fid": np.arange(next_fid, next_fid + k), "book": book,
                        "direction": rng.choice(["D", "R"], k), "c": rng.choice(self.C, k, p=self.pop_w),
                        "qty": 100 * rng.integers(1, 50, k).astype(float), "settle_i": i, "opened_i": i,
                        "trade_row": -1, "zero_control": book == "B", "contra": rng.integers(0, self.spec.n_contras, k),
                        "nt_acct": rng.integers(0, len(self.accts[book]), k), "nt_tran": rng.integers(0, 999, k),
                    }))
                    next_fid += k
            if new_parts:
                new = pd.concat(new_parts, ignore_index=True)
                registry.append(new)
                open_df = pd.concat([open_df, new], ignore_index=True) if len(open_df) else new.copy()
            if len(open_df):
                cc = open_df["c"].to_numpy(dtype=int)
                for d in "DR":
                    m = open_df["direction"].to_numpy() == d
                    np.add.at(open_cnt[d][i], cc[m], 1)
                    for b in "BP":
                        mb = m & (open_df["book"].to_numpy() == b)
                        np.add.at(open_qty[(b, d)][i], cc[mb], open_df["qty"].to_numpy(dtype=float)[mb])
                snaps.append(pd.DataFrame({"fid": open_df["fid"].to_numpy(dtype=int), "i": i,
                                           "qty_now": open_df["qty"].to_numpy(dtype=float)}))
        self.open_cnt, self.open_qty = open_cnt, open_qty
        return pd.concat(registry, ignore_index=True), pd.concat(snaps, ignore_index=True)

    def build(self) -> dict[str, pd.DataFrame]:
        self.securities()
        self.parties()
        bt = self.trades("B")
        pt = self.trades("P")
        pt["row"] += len(bt)
        all_trades = pd.concat([bt, pt], ignore_index=True)
        registry, snaps = self.simulate_fails(all_trades)
        out = {
            "business_calendar": self._calendar(),
            "beta_trades": self._beta_trades(bt),
            "p3_trades": self._p3_trades(pt),
        }
        out["beta_fail_snapshots"], out["p3_fail_snapshots"] = self._fail_snapshots(registry, snaps, all_trades, out)
        out.update(self._security_sources())
        out["beta_id_confirms"] = self._confirms(bt, out["beta_trades"])
        return out

    def _calendar(self) -> pd.DataFrame:
        flags = self.cal.month_end_flags()
        nxt = self.days.shift(-1)
        return pd.DataFrame({
            "ActualDate": self.days, "FileDate": self.days.dt.strftime("%Y%m%d"), "SettleDate": nxt,
            "NextSettleDate": self.days.shift(-2), "IsMonthEnd": flags["is_month_end"].astype(bool),
            "IsQuarterEnd": flags["is_quarter_end"].astype(int),
            "IsYearEnd": (flags["is_month_end"].astype(bool) & self.days.dt.month.eq(12)),
            "DataComplete": True, "P3DataComplete": True,
        })

    def _beta_trades(self, t: pd.DataFrame) -> pd.DataFrame:
        acct = self.accts["B"].iloc[t["a"]].reset_index(drop=True)
        sec = self.sec.iloc[t["c"]].reset_index(drop=True)
        trade_date = self.days.iloc[t["t"]].reset_index(drop=True)
        entry_date = self.days.iloc[t["e"]].reset_index(drop=True)
        control = 1000 + t["seq_in_day"].to_numpy()
        entry_hour = np.where(t["e"] > t["t"], 8.0 + 2 * self.rng.random(len(t)), np.minimum(t["exec_hour"] + 0.05, 23.5))
        return pd.DataFrame({
            "ID": np.arange(1, len(t) + 1), "TradeDate": trade_date, "SettleDate": self.days.iloc[t["s"]].to_numpy(),
            "AsOfDate": np.where(t["e"] > t["t"], entry_date, pd.NaT),
            "TradeTimestamp": _fmt_ts(entry_date, entry_hour).to_numpy(),
            "ExecutionTime": [f"{int(h):02d}:{int((h % 1) * 60):02d}" for h in t["exec_hour"]],
            "ControlNumber": control, "BETAUniqueKey": [f"BK{a:05d}{b:011d}" for a, b in zip(t["t"], t["row"])],
            "AccountNumber": acct["number"].astype(int).to_numpy(), "AccountType": acct["acct_type"].astype(int).to_numpy(),
            "AccountClass": "A", "NonCustomerAccount": acct["broker"].map({True: "Y", False: "N"}).to_numpy(),
            "SecurityNumber": sec["security_number"].to_numpy(), "CUSIP": sec["cusip9"].to_numpy(),
            "SecurityType": np.where(self.rng.random(len(t)) < 0.02, "P", sec["product"].map(BETA_SEC).to_numpy()),
            "Symbol": sec["symbol"].to_numpy(), "TradeQuantity": t["qty"].to_numpy(), "TradePrice": t["price"].round(4).to_numpy(),
            "NetAmount": t["notional"].round(2).to_numpy(), "PurchaseOrSale": np.where(t["direction"] == "D", "P", "S"),
            "FirmCapacityCode": np.where(acct["broker"], "P", "A"),
            "StandingInstructionCode": np.where(acct["dvp"], "5", np.where(acct["broker"], "7", "1")),
            "ContraDealerNumber": 0, "ContraBrokerNumber": self.beta_contras[t["contra"]],
            "foreign_curr": 0, "ProductCode": sec["product"].to_numpy(), "Cancelled": t["cancelled"].to_numpy(),
            "CancelReasonCode": np.where(t["cancelled"], "C", ""), "RebilledTrade": "N", "SyndicateTrade": "N",
        })

    def _p3_trades(self, t: pd.DataFrame) -> pd.DataFrame:
        acct = self.accts["P"].iloc[t["a"]].reset_index(drop=True)
        sec = self.sec.iloc[t["c"]].reset_index(drop=True)
        n = len(t)
        sec_type = np.where(self.rng.random(n) < 0.02, "5", sec["product"].map(P3_SEC).to_numpy())
        base = pd.DataFrame({
            "FIRM_NBR": "01", "ACCT_NBR": acct["number"].to_numpy(),
            "CCURRENCY": np.where(t["non_usd"], "CAD", "USD"), "ACCT_TYPE": acct["acct_type"].to_numpy(),
            "TRADE_FULL_DATE": self.days.iloc[t["t"]].to_numpy(), "SETTLE_FULL_DATE": self.days.iloc[t["s"]].to_numpy(),
            "SEQ_NBR": 1, "MARKET": "1", "TRAN_NBR": [np.base_repr(v, 36).rjust(4, "0") for v in t["seq_in_day"]],
            "SIDE_CODE": "C", "SIDE_SEQ_NBR": 1, "BUY_SELL": np.where(t["direction"] == "D", "B", "S"),
            "CORRECTION_CODE": "", "CXL_COR_REASON": "", "UNMATCHED_CANCEL": "",
            "CAPACITY_CODE": np.where(acct["broker"], "P", "A"), "CUST_TYPE": np.where(acct["broker"], "B", "C"),
            "ID_TRADE_IND": np.where(acct["dvp"], "Y", "N"), "CUSIP_NUMBER": sec["cusip9"].str.ljust(12).to_numpy(),
            "SEC_TYPE": sec_type, "SEC_SUB_TYPE": "", "QUANTITY": t["qty"].to_numpy(),
            "PRICE": t["price"].round(6).to_numpy(), "NET_AMOUNT": t["notional"].round(2).to_numpy(),
            "CONTRA_ACCT_NBR_1": self.p3_contras[t["contra"]], "CONTRA_ACCT_TYPE_1": "1",
            "DEL_CODE": np.where(acct["dvp"], "V", "C"), "PAY_CODE": "", "SPECIAL_PAY_DEL": "",
            "EXEC_HH": [f"{int(h):02d}" for h in t["exec_hour"]], "EXEC_MM": [f"{int((h % 1) * 60):02d}" for h in t["exec_hour"]],
            "ENTRY_DATE": self.days.iloc[t["e"]].to_numpy(), "ENTRY_TIME": 0, "SETTLE_DATE_STATUS": "",
            "TBA_FAIL_REASON": "",
        })
        cancelled = t["cancelled"].to_numpy()
        original = base[cancelled].copy()
        base.loc[cancelled, "SEQ_NBR"] = 2
        base.loc[cancelled, "CXL_COR_REASON"] = "C"
        corrected = ~cancelled & (self.rng.random(n) < 0.03)
        prior = base[corrected].copy()
        prior["QUANTITY"] = prior["QUANTITY"] * 2
        base.loc[corrected, "SEQ_NBR"] = 2
        base.loc[corrected, "CORRECTION_CODE"] = "C"
        return pd.concat([base, original, prior], ignore_index=True)

    def _fail_snapshots(self, registry, snaps, trades, out):
        rng = self.rng
        label_start = pd.Timestamp(self.spec.label_start)
        window = np.flatnonzero(self.days >= label_start)
        skip_b = set(rng.choice(window[5:-5], self.spec.missing_beta_snapshots, replace=False)) if len(window) > 20 else set()
        skip_p = set(rng.choice(window[5:-5], self.spec.missing_p3_snapshots, replace=False)) if len(window) > 20 else set()
        rows = snaps.merge(registry, on="fid")
        rows = rows[~((rows["book"] == "B") & rows["i"].isin(skip_b)) & ~((rows["book"] == "P") & rows["i"].isin(skip_p))]
        rows = rows.reset_index(drop=True)
        snap_day = self.days.iloc[rows["i"]].reset_index(drop=True)
        settle_day = self.days.iloc[rows["settle_i"]].reset_index(drop=True)
        q = self.contra_q[rows["contra"].to_numpy(dtype=int)]
        sec = self.sec.iloc[rows["c"].to_numpy(dtype=int)].reset_index(drop=True)

        bmask = rows["book"] == "B"
        bt = out["beta_trades"].set_index(pd.Index(trades.loc[trades["book"] == "B", "row"].to_numpy()))
        b = rows[bmask].reset_index(drop=True)
        b_has = (b["trade_row"] >= 0).to_numpy()
        b_tr = bt.reindex(b["trade_row"].where(b_has, bt.index[0]).astype(int)).reset_index(drop=True)
        b_sec = sec[bmask.to_numpy()].reset_index(drop=True)
        n = len(b)
        acct_rand = self.accts["B"].iloc[b["nt_acct"].clip(lower=0).to_numpy(dtype=int)].reset_index(drop=True)
        trade_date_nt = self.cal.add(settle_day[bmask.to_numpy()].reset_index(drop=True), -1)
        beta = pd.DataFrame({
            "SnapShotDate": snap_day[bmask.to_numpy()].to_numpy(),
            "FailUniqueId": 10**15 + b["fid"].to_numpy(dtype=np.int64),
            "ControlNumber": np.where(b["zero_control"].astype(bool), 0, b_tr["ControlNumber"].fillna(0)).astype(int),
            "TradeDate": np.where(b_has, b_tr["TradeDate"], trade_date_nt),
            "SettleDate": settle_day[bmask.to_numpy()].to_numpy(),
            "AsOfDate": pd.NaT,
            "AccountNumber": np.where(b_has, b_tr["AccountNumber"], acct_rand["number"].astype(int)).astype(int),
            "AccountType": np.where(b_has, b_tr["AccountType"], acct_rand["acct_type"].astype(int)).astype(int),
            "SecurityNumber": b_sec["security_number"].to_numpy(),
            "CUSIP": b_sec["cusip9"].to_numpy(),
            "SecurityTypeCode": np.where(b_has, b_tr["SecurityType"], b_sec["product"].map(BETA_SEC)),
            "ReceiveDeliverCode": np.where(b["direction"] == "D", 0, 1),
            "Quantity": np.where(b_has, b_tr["TradeQuantity"], b["qty"]),
            "PartialQuantity": 0.0,
            "Amount": np.where(b_has, b_tr["NetAmount"], b["qty"] * 50.0),
            "Price": np.where(b_has, b_tr["TradePrice"], 50.0),
            "NSCCNumber": self.beta_contras[b["contra"].to_numpy(dtype=int)],
            "DTC_CNS_Eligibility": "Y", "AffirmedId": "",
            "FailConditionCode": rng.choice(["01", "02", "03"], n),
            "FailWho": rng.choice(["C", "S"], n),
            "DKCount": np.where(b["direction"] == "R", rng.poisson(np.exp(0.5 * q[bmask.to_numpy()])), 0),
            "DTCReasonCode": rng.choice(["DK", "NO", "PS"], n),
            "StockLoanRate": np.where(b["direction"] == "D", np.round(rng.gamma(2, 1, n), 2), np.nan),
            "UpdateDateTime": snap_day[bmask.to_numpy()].to_numpy() + np.timedelta64(25, "h") + np.timedelta64(30, "m"),
        })

        pmask = ~bmask
        p = rows[pmask].reset_index(drop=True)
        p_has = (p["trade_row"] >= 0).to_numpy()
        pt_all = out["p3_trades"]
        pt = pt_all[pt_all["CXL_COR_REASON"].eq("")].sort_values("SEQ_NBR").drop_duplicates(
            ["TRADE_FULL_DATE", "ACCT_NBR", "TRAN_NBR"], keep="last")
        p_rows = trades.loc[trades["book"] == "P"].reset_index(drop=True)
        pt_key = pd.DataFrame({"row": p_rows["row"].to_numpy(),
                               "TRADE_FULL_DATE": self.days.iloc[p_rows["t"]].to_numpy(),
                               "ACCT_NBR": self.accts["P"].iloc[p_rows["a"]]["number"].to_numpy(),
                               "TRAN_NBR": [np.base_repr(v, 36).rjust(4, "0") for v in p_rows["seq_in_day"]]})
        pt = pt_key.merge(pt, on=["TRADE_FULL_DATE", "ACCT_NBR", "TRAN_NBR"], how="left").set_index("row")
        p_tr = pt.reindex(p["trade_row"].where(p_has, pt.index[0]).astype(int)).reset_index(drop=True)
        p_sec = sec[pmask.to_numpy()].reset_index(drop=True)
        m = len(p)
        sign = np.where(p["direction"] == "D", 1.0, -1.0)
        acct_rand = self.accts["P"].iloc[p["nt_acct"].clip(lower=0).to_numpy(dtype=int)].reset_index(drop=True)
        p_settle = settle_day[pmask.to_numpy()].reset_index(drop=True)
        price = np.where(p_has, p_tr["PRICE"], 50.0)
        p3 = pd.DataFrame({
            "ID": np.arange(1, m + 1), "SnapShotDate": snap_day[pmask.to_numpy()].to_numpy(), "FIRM_NBR": "01",
            "CUSIP_NUMBER": p_sec["cusip9"].str.ljust(12).to_numpy(),
            "RECORD_TYPE": np.where(p_has, "F", "T"),
            "ACCT_NBR": np.where(p_has, p_tr["ACCT_NBR"], acct_rand["number"]),
            "CCURRENCY": np.where(p_has, p_tr["CCURRENCY"], "USD"),
            "ACCT_TYPE": np.where(p_has, p_tr["ACCT_TYPE"], acct_rand["acct_type"]),
            "TRADE_FULL_DATE": np.where(p_has, p_tr["TRADE_FULL_DATE"], self.cal.add(p_settle, -1)),
            "MARKET": "1",
            "TRAN_NBR": np.where(p_has, p_tr["TRAN_NBR"], ["Z" + f"{v:03d}" for v in p["nt_tran"].clip(lower=0).astype(int)]),
            "QUANTITY": sign * p["qty_now"].to_numpy(),
            "ORIGINAL_QUANTITY": np.where(p_has, p_tr["QUANTITY"], p["qty"]),
            "NET_AMOUNT": sign * p["qty_now"].to_numpy() * price * np.where(p_sec["product"] == "EQ", 1.0, 0.01),
            "SETTLE_DATE": p_settle.to_numpy(),
            "ENTRY_DATE": np.where(p_has, p_tr["ENTRY_DATE"], self.cal.add(p_settle, -1)),
            "BUY_SELL": np.where(p["direction"] == "D", "B", "S"),
            "FAIL_REASON": rng.choice(["1", "2", "3"], m), "FAIL_PRODUCT": "",
            "CONTRA_ACCT_NBR_1": self.p3_contras[p["contra"].to_numpy(dtype=int)],
            "DTC_NBR": "0123", "DTC_DO_REASON_CODE": rng.choice(["10", "20", ""], m),
            "ID_TRD_MATCHING": "", "ID_TRD_AFFIRMATION": "",
        })
        return beta, p3

    def _security_sources(self) -> dict[str, pd.DataFrame]:
        rng, T, C = self.rng, self.T, self.C
        tt, cc = np.meshgrid(np.arange(T), np.arange(C), indexing="ij")
        tt, cc = tt.ravel(), cc.ravel()
        day = self.days.iloc[tt].reset_index(drop=True)
        sec = self.sec.iloc[cc].reset_index(drop=True)
        h = self.H[tt, cc]
        price = self.P[tt, cc]
        spread = 1e-4 * np.exp(1.5 + 0.6 * h + rng.normal(0, 0.3, len(h)))
        chill = lambda base: np.where(rng.random(len(h)) < _sigmoid(base + 1.3 * h), "Y", "N")
        out = {}
        out["beta_security"] = pd.DataFrame({
            "SnapShotDate": day, "SecurityNumber": sec["security_number"], "CUSIP": sec["cusip9"],
            "Symbol": sec["symbol"], "SecurityTypeCode": sec["product"].map(BETA_SEC), "SecurityClass": "",
            "LastClosePrice": price.round(4), "LastCloseDate": self.days.iloc[np.maximum(tt - 1, 0)].to_numpy(),
            "BidPrice": (price * (1 - spread / 2)).round(4), "AskPrice": (price * (1 + spread / 2)).round(4),
            "RoundLotQuantity": 100, "DTCCNSEligibilityCode": np.where(h > 2.4, "N", "Y"), "NSCCEligible": "Y",
            "DTCChillDeposit": chill(-6.0), "DTCChillCOD": chill(-6.5), "DTCChillWdl": chill(-7.0),
            "DTCChilldo": chill(-6.5), "DTCChillPledge": "N", "DTCChillSeg": "N", "DTCChillFuture": "N",
            "DTCfrozen": chill(-8.0), "MaturityDate": pd.NaT, "IPOPrice": np.nan, "BondFactor": 1.0,
            "UpdateDateTime": day + pd.Timedelta(hours=26),
        })
        hold = rng.random(C) < 0.8
        seg = hold[cc]
        beta_d = self.open_qty[("B", "D")][tt, cc]
        beta_r = self.open_qty[("B", "R")][tt, cc]
        req, avail = self.REQ[tt, cc], self.AVAIL[tt, cc]
        out["beta_segregation"] = pd.DataFrame({
            "SnapShotDate": day, "SecurityNumber": sec["security_number"], "CUSIP": sec["cusip9"],
            "Required": req.round(), "Available": avail.round(),
            "StartOfDayDeficitQty": np.maximum(req - avail, 0).round() * rng.uniform(0.5, 1.0, len(h)),
            "StockLoanQty": (avail * 0.1).round(), "StockBorrowQty": (np.maximum(req - avail, 0) * 0.5).round(),
            "DealerFailDeliverQty": beta_d, "DealerFailReceiveQty": beta_r, "CUST_FR": 0.0, "CUST_FD": 0.0,
            "CNS_1": -beta_d, "CNS_2": 0.0, "InventoryLongQuantity": avail.round(), "InventoryShortQuantity": 0.0,
            "LoannetBorrowQty": (np.maximum(req - avail, 0) * 0.5).round(), "LoannetLoanQty": (avail * 0.1).round(),
            "FrozenCode": "", "NoLoanInd": np.where(h > 2.0, "Y", "N"), "UpdateDateTime": day + pd.Timedelta(hours=26),
        })[seg].reset_index(drop=True)
        out["beta_close_history"] = pd.DataFrame({
            "TradeDate": day, "SecurityNumber": sec["security_number"], "ClosePrice": price.round(4),
            "ClosePriceDate": day, "CloseVolume": self.V[tt, cc].round(),
        })
        out["p3_eod_prices"] = pd.DataFrame({
            "SnapShotDate": day, "CUSIP_NUMBER": sec["cusip9"].str.ljust(12), "CURR_PRICE": price.round(6),
            "POOL_FACTOR": 1.0, "SECURITY_FACTOR": 1.0, "PRICE_FULL_DATE": day,
        })
        total = sec["adv"].to_numpy() * np.exp(0.5 - 0.9 * h + rng.normal(0, 0.3, len(h)))
        next_day = self.days.iloc[np.minimum(tt + 1, T - 1)].to_numpy()
        out["smartloan_avail"] = pd.DataFrame({
            "FileDate": day, "TradeDate": next_day, "CUSIP": sec["cusip9"], "TotalAvailable": total.round(),
            "AvailableFromFirm": (total * 0.3).round(), "AvailableFromCustomer": (total * 0.4).round(),
            "AvailableFromNonCustomer": (total * 0.2).round(), "AvailableFromBankLoan": (total * 0.05).round(),
            "AvailableFromReturn": (total * 0.05).round(), "OtherAvailability": 0.0, "MarketPriceClosing": price.round(4),
            "SecurityType": sec["product"], "LastUpdateDateTime": day + pd.Timedelta(hours=21),
        })
        loan_rows = rng.random(len(h)) < 0.7
        rebate = 4.3 - 1.8 * np.maximum(h, 0) + rng.normal(0, 0.4, len(h))
        loanet = []
        for side, scale in (("B", np.exp(-0.5 + 0.6 * h)), ("L", np.exp(-1.0 - 0.3 * h))):
            qty = sec["adv"].to_numpy() * scale * np.exp(rng.normal(0, 0.3, len(h)))
            loanet.append(pd.DataFrame({
                "TradeDate": day, "CUSIP": sec["cusip9"], "BorrowLoan": side, "TotalQuantity": qty.round(),
                "ContractCount": rng.poisson(4, len(h)) + 1, "ContraCount": rng.poisson(2, len(h)) + 1,
                "AvgRate": np.round(rng.uniform(0.1, 1.0, len(h)), 4), "AvgRebate": rebate.round(4),
                "MinRebate": (rebate - np.abs(rng.normal(0, 0.5, len(h)))).round(4),
            })[loan_rows])
        out["loanet_positions"] = pd.concat(loanet, ignore_index=True)
        vwar = 0.25 + 0.6 * np.exp(0.9 * h) + rng.normal(0, 0.1, len(h))
        out["borrow_rates"] = pd.DataFrame({
            "FileDate": day, "SecurityNumber": sec["security_number"], "CUSIP": sec["cusip9"],
            "NumberOfBorrows": rng.poisson(3, len(h)), "BorrowQuantity": (sec["adv"].to_numpy() * 0.05).round(),
            "BorrowVWAR": np.maximum(vwar, 0.01).round(4), "BorrowCost": 0.0,
        })
        cns = []
        for book, src in (("P", "cns_p3"), ("B", "cns_beta")):
            ftd = self.open_qty[(book, "D")][tt, cc]
            long_pos = np.where(rng.random(len(h)) < 0.25, rng.integers(100, 5000, len(h)), 0)
            pos = long_pos - ftd
            keep = pos != 0
            frame = pd.DataFrame({
                "PositionDate": day, "Cycle": "C02", "CUSIP": sec["cusip9"], "ClosingNetPosition": pos,
                "NetSettlingTrades": 0.0, "MarketValue": pos * price, "ParticipantClearingNum": "8199" if book == "P" else "0103",
                "ParticipantSubAccount": "", "UpDateDateTime": day + pd.Timedelta(hours=7),
            })[keep]
            early = frame.copy()
            early["Cycle"] = "C01"
            out[src] = pd.concat([early, frame], ignore_index=True)
        thr = np.zeros((T, C), dtype=bool)
        for t in range(1, T):
            thr[t] = np.where(self.H[t] > 1.8, True, np.where(self.H[t] < 1.2, False, thr[t - 1]))
        change = np.argwhere(thr[1:] != thr[:-1])
        th_rows = []
        for k, (t0, c) in enumerate(change):
            t = t0 + 1
            eff = self.days.iloc[min(t + 1, T - 1)]
            th_rows.append({"ID": k + 1, "SecurityNumber": int(self.sec["security_number"].iloc[c]),
                            "EffectiveDate": eff.strftime("%Y-%m-%d"),
                            "AddedTimestamp": self.days.iloc[t].strftime("%Y-%m-%d") + "-23.15.00.000000",
                            "ThresholdInd": "Y" if thr[t, c] else "N", "ThresholdTerm": "", "UpdateDateTime": self.days.iloc[t]})
        out["beta_threshold_history"] = pd.DataFrame(th_rows)
        ftd_all = self.open_qty[("B", "D")] + self.open_qty[("P", "D")]
        r_t, r_c = np.nonzero(ftd_all > 0)
        out["reg204"] = pd.DataFrame({
            "Source": "CNS", "ReportDate": self.days.iloc[r_t].to_numpy(), "File204Date": self.days.iloc[r_t].to_numpy(),
            "SettleDate": self.days.iloc[np.maximum(r_t - 1, 0)].to_numpy(), "CUSIP": self.sec["cusip9"].iloc[r_c].to_numpy(),
            "SecurityType": self.sec["product"].iloc[r_c].to_numpy(), "Category": "", "AccountType": "",
            "BorrowDesc": np.where(self.H[r_t, r_c] > 1.0, "HTB", "ETB"),
            "ThresholdIndex": np.where(thr[r_t, r_c], "Y", "N"),
            "SOD_CNS_FailDayQty": ftd_all[r_t, r_c], "FailAmount": ftd_all[r_t, r_c] * self.P[r_t, r_c],
            "SettleQty": 0, "IsWarrant": "N",
        })
        return out

    def _confirms(self, t: pd.DataFrame, beta_trades: pd.DataFrame) -> pd.DataFrame:
        dvp = t["dvp"].to_numpy() & ~t["cancelled"].to_numpy()
        bt = beta_trades[dvp].reset_index(drop=True)
        tt = t[dvp].reset_index(drop=True)
        return pd.DataFrame({
            "SnapshotDate": bt["TradeDate"], "BETAUniqueKey": "C" + bt["BETAUniqueKey"].str[1:],
            "BetaTradeUniqueKey": bt["BETAUniqueKey"], "TradeDate": bt["TradeDate"], "SettleDate": bt["SettleDate"],
            "CUSIP": bt["CUSIP"], "Quantity": bt["TradeQuantity"], "AccountNumber": bt["AccountNumber"],
            "MatchedIndicator": np.where(tt["matched"], "Y", "N"),
            "ConfirmAffirmIndicator": np.where(tt["affirmed"], "A", "C"),
            "AffirmPartyIndicator": np.where(tt["affirmed"], "I", ""), "DTCEligibleIndicator": "Y",
            "CancelIndicator": "N", "StatusIndicator": np.where(tt["affirmed"], "AFF", "CNF"),
            "ConfirmProcessDate": bt["TradeDate"],
        })


def generate(spec: SyntheticSpec | None = None) -> dict[str, pd.DataFrame]:
    return _Generator(spec or SyntheticSpec()).build()


def write_synthetic(raw_dir: Path, spec: SyntheticSpec | None = None) -> dict[str, int]:
    frames = generate(spec)
    for source_id, frame in frames.items():
        write_raw(frame, source_id, raw_dir, name="synthetic")
    return {k: len(v) for k, v in frames.items()}
