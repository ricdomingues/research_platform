"""Scoring one feature snapshot with a trained model (Plan 5, D88).

A prediction is only meaningful next to the versions that produced it, so `Prediction` carries all of them and
the readmodels, API and dashboard pass them along untouched. Refusing to score is a first-class outcome: a
snapshot built by a different `feature_version`, or one whose encoded layout does not match the model's, raises
instead of quietly producing a number from misaligned columns — the failure mode that makes a model look
confident and be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any

from virtual_orders.research.features import FeatureSnapshot
from virtual_orders.research.ml.dataset import encode
from virtual_orders.research.ml.model import GbdtModel
from virtual_orders.research.models import quantized

PROBABILITY_QUANTUM = Decimal("0.0001")
PROBABILITY_MEANING = (
    "P(target reached before stop) for this setup's ATR barriers, under the label version named beside it. "
    "It is a model estimate over historical occurrences, not a guarantee and not an expected return."
)


class FeatureLayoutMismatch(ValueError):
    """The snapshot cannot be scored by this model: versions or column layouts differ."""


@dataclass(frozen=True)
class Prediction:
    probability_target_first: Decimal
    model_version: str
    feature_version: str
    label_version: str
    meaning: str

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def predict(
    model: GbdtModel,
    snapshot: FeatureSnapshot,
    *,
    model_version: str,
    feature_version: str,
    label_version: str,
) -> Prediction:
    """Score one snapshot. Raises `FeatureLayoutMismatch` rather than score a snapshot it does not fit."""
    if snapshot.feature_version != feature_version:
        raise FeatureLayoutMismatch(
            f"model expects {feature_version}, snapshot is {snapshot.feature_version}"
        )
    row = tuple(encode(snapshot))
    if len(row) != len(model.columns):
        raise FeatureLayoutMismatch(f"model expects {len(model.columns)} columns, snapshot encodes {len(row)}")
    probability = model.predict_proba([row])[0]
    return Prediction(
        probability_target_first=quantized(Decimal(repr(probability)), PROBABILITY_QUANTUM),
        model_version=model_version,
        feature_version=feature_version,
        label_version=label_version,
        meaning=PROBABILITY_MEANING,
    )
