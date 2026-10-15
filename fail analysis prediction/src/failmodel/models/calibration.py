"""Probability calibration: isotonic, or sigmoid (Platt on the logit) when positives are scarce."""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    return np.log(p / (1 - p))


class Calibrator:
    """``method=None`` picks isotonic when there are at least ``min_positives_isotonic`` positives."""

    def __init__(self, min_positives_isotonic: int = 500, method: str | None = None):
        self.min_positives_isotonic = min_positives_isotonic
        self.requested = method
        self.method: str | None = None
        self._model = None
        self.fit_rows = 0
        self.fit_positives = 0

    def fit(self, raw: np.ndarray, y: np.ndarray) -> "Calibrator":
        raw, y = np.asarray(raw, dtype=float), np.asarray(y, dtype=int)
        self.fit_rows, self.fit_positives = int(len(y)), int(y.sum())
        isotonic = self.requested == "isotonic" or (self.requested is None and self.fit_positives >= self.min_positives_isotonic)
        if isotonic:
            self.method = "isotonic"
            self._model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, y)
        else:
            self.method = "sigmoid"
            self._model = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(raw).reshape(-1, 1), y)
        return self

    def predict(self, raw: np.ndarray) -> np.ndarray:
        raw = np.asarray(raw, dtype=float)
        if self.method == "isotonic":
            return self._model.predict(raw)
        return self._model.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]

    def describe(self) -> dict:
        return {"method": self.method, "fit_rows": self.fit_rows, "fit_positives": self.fit_positives}
