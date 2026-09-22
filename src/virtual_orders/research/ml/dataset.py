"""Turning leak-safe occurrences into a model matrix (Plan 5, D84).

This is the documented boundary where Decimal becomes float: a Decimal is exact and a model is not, so the
conversion happens once, here, in one place that can be pointed at.

Two design choices keep the dataset honest:

* **The column layout is fixed in code, never learned from data.** Numeric columns come from
  `FeatureSnapshot.NUMERIC_COLUMNS`; categorical ones are one-hot encoded against vocabularies that are
  enumerations (every pattern name, every trend state), not the values that happen to appear in this sample.
  A dataset built from ten rows therefore has exactly the same columns as one built from ten thousand, and a
  model trained on either can score the other.
* **A missing value stays missing.** Nothing is imputed with a mean or a median, because computing one over a
  whole dataset is a quiet way of leaking the test period into training. Missing values are passed through as
  NaN and the tree learns which way to send them.

Unresolved barriers (a timeout, or a window that ran out of candles) are **excluded and counted**, never
folded in as negatives: "the target was not reached within twenty candles" is not the same fact as "the stop
was hit", and treating it as one would quietly bias every probability the model produces.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import Any

from core.domain.hashing import sha256_hex
from virtual_orders.analytics.pressure import PressureSide
from virtual_orders.research.backtest import Occurrence
from virtual_orders.research.candlesticks import SUPPORTED_PATTERNS
from virtual_orders.research.features import FeatureSnapshot
from virtual_orders.research.labels import LABEL_VERSION
from virtual_orders.research.market_structure import BreakoutState, GapState
from virtual_orders.research.models import PatternDirection, Timeframe, TrendState

MISSING = float("nan")

# Fixed vocabularies: enumerations, not whatever the sample happened to contain.
VOCABULARIES: dict[str, tuple[str, ...]] = {
    "pattern": tuple(sorted(SUPPORTED_PATTERNS)),
    "pattern_direction": tuple(item.value for item in PatternDirection),
    "short_term_trend": tuple(item.value for item in TrendState),
    "medium_term_trend": tuple(item.value for item in TrendState),
    "breakout": tuple(item.value for item in BreakoutState),
    "gap": tuple(item.value for item in GapState),
    "pressure_side": tuple(item.value for item in PressureSide),
    "timeframe": tuple(item.value for item in Timeframe),
}


def column_names() -> list[str]:
    """The model's input layout, in a fixed order. Changing it is a `feature_version` change."""
    names = list(FeatureSnapshot.NUMERIC_COLUMNS)
    for column in FeatureSnapshot.CATEGORICAL_COLUMNS:
        names.extend(f"{column}={value}" for value in VOCABULARIES[column])
    return names


def encode(snapshot: FeatureSnapshot) -> list[float]:
    """One feature snapshot as one row, with missing values left as NaN."""
    row: list[float] = []
    for value in snapshot.numeric().values():
        row.append(MISSING if value is None else float(value))
    categorical = snapshot.categorical()
    for column in FeatureSnapshot.CATEGORICAL_COLUMNS:
        present = categorical.get(column)
        row.extend(1.0 if present == value else 0.0 for value in VOCABULARIES[column])
    return row


@dataclass(frozen=True)
class Dataset:
    """A matrix and its provenance. `rows` never contains an unresolved barrier."""

    feature_version: str
    label_version: str
    columns: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]
    labels: tuple[int, ...]
    keys: tuple[str, ...]
    timestamps: tuple[datetime, ...]
    positives: int
    negatives: int
    excluded_unresolved: int

    def __post_init__(self) -> None:
        if len(self.rows) != len(self.labels) or len(self.rows) != len(self.keys):
            raise ValueError("rows, labels and keys must line up")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every row must match the column layout")

    @property
    def samples(self) -> int:
        return len(self.rows)

    @property
    def balance(self) -> Decimal | None:
        """Share of positives, or None with no rows: a probability model needs its base rate stated."""
        if not self.samples:
            return None
        return Decimal(self.positives) / Decimal(self.samples)

    @property
    def layout_hash(self) -> str:
        """Identity of the input layout: a model may only score a dataset whose layout hash it was trained on."""
        return sha256_hex({"feature_version": self.feature_version, "columns": list(self.columns)})

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)
                if item.name not in ("rows", "labels", "keys", "timestamps")}


def build_dataset(
    occurrences: Sequence[Occurrence], *, feature_version: str | None = None, label_version: str = LABEL_VERSION
) -> Dataset:
    """Encode resolved occurrences into a matrix, in chronological order.

    Chronological order is not cosmetic: every split in `backtest.py` is a time boundary, so a dataset that
    arrived shuffled could not be split without re-sorting it, and a caller might forget.
    """
    ordered = sorted(occurrences, key=lambda item: (item.signal_end_ts, item.ticker, item.pattern))
    columns = tuple(column_names())
    rows: list[tuple[float, ...]] = []
    labels: list[int] = []
    keys: list[str] = []
    stamps: list[datetime] = []
    excluded = 0
    versions = {item.features.feature_version for item in ordered}
    if len(versions) > 1:
        raise ValueError(f"a dataset cannot mix feature versions: {sorted(versions)}")
    for item in ordered:
        outcome = None if item.barrier is None else item.barrier.target_first
        if outcome is None:
            excluded += 1  # a timeout is not a loss: it is an absence of a resolved barrier
            continue
        rows.append(tuple(encode(item.features)))
        labels.append(1 if outcome else 0)
        keys.append(f"{item.ticker}:{item.timeframe.value}:{item.pattern}:{item.signal_end_ts.isoformat()}")
        stamps.append(item.signal_end_ts)
    positives = sum(labels)
    return Dataset(
        feature_version=feature_version or (versions.pop() if versions else "unknown"),
        label_version=label_version,
        columns=columns,
        rows=tuple(rows),
        labels=tuple(labels),
        keys=tuple(keys),
        timestamps=tuple(stamps),
        positives=positives,
        negatives=len(labels) - positives,
        excluded_unresolved=excluded,
    )
