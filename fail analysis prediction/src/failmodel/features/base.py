from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..bizcal import BusinessCalendar
from ..config import Settings


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    family: str
    source: str
    asof_rule: str
    books: str
    kind: str
    description: str


@dataclass
class FeatureContext:
    """Inputs shared by the feature families.

    ``universe`` holds every canonical trade with its scoring date and exclusion reason; ``history`` holds
    labels for every settle date in the extract window (history features filter them by availability).
    """

    raw: dict[str, pd.DataFrame]
    cal: BusinessCalendar
    settings: Settings
    fails: pd.DataFrame
    universe: pd.DataFrame
    history: pd.DataFrame


def num(name, family, source, asof_rule, books, description) -> FeatureSpec:
    return FeatureSpec(name, family, source, asof_rule, books, "numeric", description)


def cat(name, family, source, asof_rule, books, description) -> FeatureSpec:
    return FeatureSpec(name, family, source, asof_rule, books, "categorical", description)
