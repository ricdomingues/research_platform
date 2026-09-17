"""Training and honest evaluation of the research classifier (Plan 5, D87).

Every number a model is judged by comes from data the model never trained on. The entry point is walk-forward:
fold *k* trains on purged history and is scored on the slice that follows it, so the reported metrics are
out-of-sample by construction rather than by convention.

Metrics are converted to Decimal here. They are computed in float (they are statistics, not money), but a
metric that will be stored in JSONB has to survive the platform's canonical serializer, which refuses floats
outright — so the conversion happens once, at this boundary, instead of leaking a float into the ledger.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np

from virtual_orders.research.backtest import Fold, Occurrence, walk_forward
from virtual_orders.research.ml.dataset import Dataset, build_dataset
from virtual_orders.research.ml.model import GbdtModel, Hyperparameters, fit
from virtual_orders.research.models import quantized

MODEL_NAME = "candle-setup-target-first"
TRAINING_VERSION = "training-v1"
METRIC_QUANTUM = Decimal("0.000001")
MIN_TRAINING_ROWS = 40  # below this a probability is noise dressed as a number


def _decimal(value: float) -> Decimal:
    return quantized(Decimal(repr(float(value))), METRIC_QUANTUM)


def _auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """Rank-based ROC AUC with averaged ties; None when one class is missing (AUC is undefined then)."""
    positives = int(labels.sum())
    negatives = int(labels.size - positives)
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(scores.size, dtype=float)
    position = 0
    while position < scores.size:
        end = position
        while end + 1 < scores.size and scores[order[end + 1]] == scores[order[position]]:
            end += 1
        average = (position + end + 2) / 2.0  # ranks are 1-based, ties share their mean rank
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return float((ranks[labels == 1].sum() - positives * (positives + 1) / 2.0) / (positives * negatives))


@dataclass(frozen=True)
class Metrics:
    """Everything needed to judge a probability model, with the sample it came from always beside it."""

    samples: int
    positives: int
    base_rate: Decimal | None
    accuracy: Decimal | None
    precision: Decimal | None
    recall: Decimal | None
    brier: Decimal | None
    log_loss: Decimal | None
    auc: Decimal | None

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


EMPTY_METRICS = Metrics(0, 0, None, None, None, None, None, None, None)


def evaluate(model: GbdtModel, rows: Sequence[tuple[float, ...]], labels: Sequence[int]) -> Metrics:
    """Score a trained model on rows it did not train on. A 0.5 threshold is used only for the count metrics."""
    if not rows:
        return EMPTY_METRICS
    probabilities = np.asarray(model.predict_proba(list(rows)), dtype=float)
    target = np.asarray(labels, dtype=float)
    predicted = probabilities > 0.5
    actual = target == 1.0
    true_positive = int((predicted & actual).sum())
    predicted_positive = int(predicted.sum())
    clipped = np.clip(probabilities, 1e-12, 1 - 1e-12)
    auc = _auc(probabilities, target)
    return Metrics(
        samples=len(rows),
        positives=int(actual.sum()),
        base_rate=_decimal(float(actual.mean())),
        accuracy=_decimal(float((predicted == actual).mean())),
        precision=_decimal(true_positive / predicted_positive) if predicted_positive else None,
        recall=_decimal(true_positive / int(actual.sum())) if int(actual.sum()) else None,
        brier=_decimal(float(np.mean((probabilities - target) ** 2))),
        log_loss=_decimal(float(-np.mean(target * np.log(clipped) + (1 - target) * np.log(1 - clipped)))),
        auc=None if auc is None else _decimal(auc),
    )


@dataclass(frozen=True)
class TrainedModel:
    """A model plus the complete provenance the registry stores. Nothing here is ever overwritten."""

    model_name: str
    model_version: str
    training_version: str
    model: GbdtModel
    feature_version: str
    label_version: str
    layout_hash: str
    training_from: datetime
    training_to: datetime
    training_rows: int
    training_metrics: Metrics
    validation_metrics: Metrics
    folds: tuple[Metrics, ...]
    created_at: datetime

    @property
    def artifact_hash(self) -> str:
        return self.model.artifact_hash

    def as_document(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name, "model_version": self.model_version,
            "training_version": self.training_version, "feature_version": self.feature_version,
            "label_version": self.label_version, "layout_hash": self.layout_hash,
            "training_from": self.training_from, "training_to": self.training_to,
            "training_rows": self.training_rows, "artifact_hash": self.artifact_hash,
            "hyperparameters": self.model.hyperparameters.as_document(),
            "training_metrics": self.training_metrics.as_document(),
            "validation_metrics": self.validation_metrics.as_document(),
            "folds": [item.as_document() for item in self.folds], "created_at": self.created_at,
        }


def model_version(artifact_hash: str, trained_to: datetime) -> str:
    """Deterministic: the same artifact trained to the same instant always names itself the same way."""
    return f"{MODEL_NAME}-{trained_to.date().isoformat()}-{artifact_hash[:12]}"


def train_dataset(
    dataset: Dataset,
    *,
    validation: Dataset | None = None,
    params: Hyperparameters | None = None,
    created_at: datetime,
    folds: Sequence[Metrics] = (),
) -> TrainedModel:
    """Fit on `dataset` and score on `validation`, which must never share rows with it."""
    if dataset.samples < MIN_TRAINING_ROWS:
        raise ValueError(f"training needs at least {MIN_TRAINING_ROWS} resolved rows, got {dataset.samples}")
    if validation is not None and validation.layout_hash != dataset.layout_hash:
        raise ValueError("validation data must share the training layout")
    model = fit(list(dataset.rows), list(dataset.labels), dataset.columns, params=params)
    return TrainedModel(
        model_name=MODEL_NAME,
        model_version=model_version(model.artifact_hash, dataset.timestamps[-1]),
        training_version=TRAINING_VERSION,
        model=model,
        feature_version=dataset.feature_version,
        label_version=dataset.label_version,
        layout_hash=dataset.layout_hash,
        training_from=dataset.timestamps[0],
        training_to=dataset.timestamps[-1],
        training_rows=dataset.samples,
        training_metrics=evaluate(model, dataset.rows, dataset.labels),
        validation_metrics=(EMPTY_METRICS if validation is None
                            else evaluate(model, validation.rows, validation.labels)),
        folds=tuple(folds),
        created_at=created_at,
    )


@dataclass(frozen=True)
class FoldReport:
    index: int
    train_rows: int
    test_rows: int
    purged: int
    metrics: Metrics


@dataclass(frozen=True)
class WalkForwardReport:
    """Out-of-sample results per fold, and the pooled metric that judges the approach as a whole."""

    training_version: str
    folds: tuple[FoldReport, ...]
    pooled: Metrics
    trainable_folds: int
    skipped_folds: int

    def as_document(self) -> dict[str, Any]:
        return {
            "training_version": self.training_version, "trainable_folds": self.trainable_folds,
            "skipped_folds": self.skipped_folds, "pooled": self.pooled.as_document(),
            "folds": [{"index": item.index, "train_rows": item.train_rows, "test_rows": item.test_rows,
                       "purged": item.purged, "metrics": item.metrics.as_document()} for item in self.folds],
        }


def _fold_datasets(fold: Fold) -> tuple[Dataset, Dataset]:
    return build_dataset(fold.train.occurrences), build_dataset(fold.test.occurrences)


def run_walk_forward(
    occurrences: Sequence[Occurrence],
    *,
    folds: int = 4,
    params: Hyperparameters | None = None,
    embargo: Any = None,
) -> WalkForwardReport:
    """Train and score fold by fold, never once on data the fold's test slice contains.

    A fold whose training set is too small, or whose labels are all one class, is **skipped and counted**
    rather than filled in with a model trained on something else: a missing number is honest, an invented one
    is not.
    """
    from datetime import timedelta  # local import: the default embargo is a value, not a module-level policy

    window = timedelta(0) if embargo is None else embargo
    reports: list[FoldReport] = []
    skipped = 0
    pooled_scores: list[float] = []
    pooled_labels: list[int] = []
    for fold in walk_forward(occurrences, folds=folds, embargo=window):
        training, testing = _fold_datasets(fold)
        if training.samples < MIN_TRAINING_ROWS or len(set(training.labels)) < 2 or not testing.samples:
            skipped += 1
            continue
        model = fit(list(training.rows), list(training.labels), training.columns, params=params)
        metrics = evaluate(model, testing.rows, testing.labels)
        reports.append(FoldReport(fold.index, training.samples, testing.samples, fold.train.purged, metrics))
        pooled_scores.extend(model.predict_proba(list(testing.rows)))
        pooled_labels.extend(testing.labels)
    pooled = EMPTY_METRICS
    if pooled_scores:
        scores = np.asarray(pooled_scores, dtype=float)
        target = np.asarray(pooled_labels, dtype=float)
        predicted, actual = scores > 0.5, target == 1.0
        true_positive = int((predicted & actual).sum())
        predicted_positive = int(predicted.sum())
        clipped = np.clip(scores, 1e-12, 1 - 1e-12)
        auc = _auc(scores, target)
        pooled = Metrics(
            samples=len(pooled_scores), positives=int(actual.sum()), base_rate=_decimal(float(actual.mean())),
            accuracy=_decimal(float((predicted == actual).mean())),
            precision=_decimal(true_positive / predicted_positive) if predicted_positive else None,
            recall=_decimal(true_positive / int(actual.sum())) if int(actual.sum()) else None,
            brier=_decimal(float(np.mean((scores - target) ** 2))),
            log_loss=_decimal(float(-np.mean(target * np.log(clipped) + (1 - target) * np.log(1 - clipped)))),
            auc=None if auc is None else _decimal(auc),
        )
    return WalkForwardReport(TRAINING_VERSION, tuple(reports), pooled, len(reports), skipped)
