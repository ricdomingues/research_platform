"""The classifier (spec 16, 24): a fixed column layout, deterministic training and honest refusals.

Three properties are load-bearing here. The column layout comes from code and not from the sample, so a model
trained on one window can score another. Training is a pure function of its inputs, so the same rows always
produce the same artifact hash. And a model refuses to score a snapshot it does not fit, instead of silently
producing a confident number from misaligned columns.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from core.domain.hashing import canonical_json
from tests.research.support import BASE, zigzag
from virtual_orders.research.backtest import build_occurrences, time_splits
from virtual_orders.research.ml.dataset import MISSING, build_dataset, column_names, encode
from virtual_orders.research.ml.inference import FeatureLayoutMismatch, predict
from virtual_orders.research.ml.model import MODEL_KIND, GbdtModel, Hyperparameters, fit
from virtual_orders.research.ml.training import (
    MIN_TRAINING_ROWS,
    evaluate,
    model_version,
    run_walk_forward,
    train_dataset,
)

FAST = Hyperparameters(n_estimators=8, max_depth=2, min_samples_leaf=5)


def occurrences(cycles=5):
    return build_occurrences(zigzag(cycles=cycles), ticker="AAPL", data_as_of=BASE)


def separable(rows=240):
    """A signal a tree can actually learn, with a missing value in every eleventh row."""
    matrix, labels = [], []
    for index in range(rows):
        first = (index % 20) / 20.0
        third = MISSING if index % 11 == 0 else (index % 5) / 5.0
        matrix.append((first, float(index % 7), third, 1.0 if index % 2 else 0.0))
        labels.append(1 if first > 0.5 else 0)
    return matrix, labels, ("a", "b", "c", "flag")


# --- dataset -----------------------------------------------------------------------------------------------
def test_the_column_layout_comes_from_code_not_from_the_sample():
    small = build_dataset(occurrences(cycles=2))
    large = build_dataset(occurrences(cycles=5))
    assert small.columns == large.columns == tuple(column_names())
    assert small.layout_hash == large.layout_hash  # so a model trained on one can score the other


def test_an_unresolved_barrier_is_excluded_and_counted_never_treated_as_a_loss():
    items = occurrences()
    data = build_dataset(items)
    assert data.samples + data.excluded_unresolved == len(items)
    assert data.positives + data.negatives == data.samples
    assert data.balance is not None and 0 <= data.balance <= 1


def test_a_missing_feature_stays_missing_rather_than_being_imputed():
    (occurrence,) = occurrences(cycles=2)[:1]
    row = encode(occurrence.features)
    assert len(row) == len(column_names())
    # Early candles have no EMA 200, and that absence is passed through as NaN, not as a zero or a mean.
    index = column_names().index("ema200_distance_pct")
    assert row[index] != row[index] or occurrence.features.ema200_distance_pct is not None


def test_a_dataset_never_mixes_feature_versions():
    items = occurrences(cycles=2)
    altered = replace(items[0], features=replace(items[0].features, feature_version="features-v9"))
    with pytest.raises(ValueError, match="cannot mix feature versions"):
        build_dataset([altered, *items[1:]])


def test_rows_arrive_in_chronological_order():
    data = build_dataset(occurrences())
    assert list(data.timestamps) == sorted(data.timestamps)


# --- model -------------------------------------------------------------------------------------------------
def test_the_model_learns_a_separable_signal_and_handles_missing_values():
    matrix, labels, columns = separable()
    model = fit(matrix, labels, columns, params=Hyperparameters(n_estimators=25, max_depth=3))
    probabilities = model.predict_proba(matrix)
    assert all(0.0 <= value <= 1.0 for value in probabilities)
    correct = sum((value > 0.5) == bool(label) for value, label in zip(probabilities, labels, strict=True))
    assert correct / len(labels) > 0.9
    assert model.kind == MODEL_KIND and model.framework == "numpy"


def test_training_is_deterministic_to_the_artifact_hash():
    matrix, labels, columns = separable()
    first = fit(matrix, labels, columns, params=FAST)
    second = fit(matrix, labels, columns, params=FAST)
    assert first.artifact_hash == second.artifact_hash
    assert first.predict_proba(matrix) == second.predict_proba(matrix)


def test_a_model_round_trips_through_its_document_without_changing_a_prediction():
    matrix, labels, columns = separable()
    model = fit(matrix, labels, columns, params=FAST)
    restored = GbdtModel.from_document(model.as_document())
    assert restored.predict_proba(matrix) == model.predict_proba(matrix)
    assert restored.artifact_hash == model.artifact_hash
    # The document survives the platform's canonical serializer: floats are stored as exact text.
    assert canonical_json(model.as_document())


def test_the_model_refuses_what_it_cannot_learn_or_score():
    matrix, labels, columns = separable()
    with pytest.raises(ValueError, match="both classes"):
        fit(matrix, [1] * len(labels), columns, params=FAST)
    with pytest.raises(ValueError, match="at least one row"):
        fit([], [], columns, params=FAST)
    model = fit(matrix, labels, columns, params=FAST)
    with pytest.raises(ValueError, match="expected 4 columns"):
        model.predict_proba([(1.0, 2.0)])


def test_hyperparameters_are_validated():
    for bad in ({"n_estimators": 0}, {"learning_rate": 0}, {"max_depth": 0}, {"min_samples_leaf": 0},
                {"l2_regularization": -1}, {"max_bins": 1}):
        with pytest.raises(ValueError):
            Hyperparameters(**bad)


# --- training and evaluation -------------------------------------------------------------------------------
def test_metrics_are_decimal_so_they_survive_the_storage_boundary():
    matrix, labels, columns = separable()
    metrics = evaluate(fit(matrix, labels, columns, params=FAST), matrix, labels)
    assert isinstance(metrics.accuracy, Decimal) and isinstance(metrics.brier, Decimal)
    assert metrics.samples == len(labels) and metrics.positives == sum(labels)
    assert canonical_json(metrics.as_document())


def test_auc_is_undefined_with_a_single_class_rather_than_invented():
    matrix, labels, columns = separable()
    model = fit(matrix, labels, columns, params=FAST)
    one_class = [row for row, label in zip(matrix, labels, strict=True) if label == 1]
    assert evaluate(model, one_class, [1] * len(one_class)).auc is None


def test_training_needs_a_minimum_sample_and_a_matching_validation_layout():
    train, validation, _ = time_splits(occurrences())
    training_data, validation_data = build_dataset(train.occurrences), build_dataset(validation.occurrences)
    if training_data.samples < MIN_TRAINING_ROWS:
        pytest.skip("fixture produced too few resolved rows for this assertion")
    trained = train_dataset(training_data, validation=validation_data, created_at=BASE, params=FAST)
    assert trained.training_rows == training_data.samples
    assert trained.model_version == model_version(trained.artifact_hash, training_data.timestamps[-1])
    assert trained.validation_metrics.samples == validation_data.samples
    assert canonical_json(trained.as_document())
    with pytest.raises(ValueError, match="at least"):
        train_dataset(build_dataset(train.occurrences[:3]), created_at=BASE, params=FAST)


def test_walk_forward_scores_every_fold_on_data_it_did_not_train_on():
    report = run_walk_forward(occurrences(), folds=4, params=FAST)
    assert report.trainable_folds + report.skipped_folds == 3
    for fold in report.folds:
        assert fold.test_rows > 0 and fold.train_rows >= MIN_TRAINING_ROWS
    if report.trainable_folds:
        assert report.pooled.samples == sum(fold.test_rows for fold in report.folds)
        assert report.pooled.base_rate is not None
    assert canonical_json(report.as_document())


# --- inference ---------------------------------------------------------------------------------------------
def test_a_prediction_carries_every_version_it_depends_on():
    items = occurrences()
    data = build_dataset(items)
    model = fit(list(data.rows), list(data.labels), data.columns, params=FAST)
    prediction = predict(model, items[0].features, model_version="m-1", feature_version=data.feature_version,
                         label_version=data.label_version)
    assert Decimal(0) <= prediction.probability_target_first <= Decimal(1)
    assert (prediction.model_version, prediction.feature_version) == ("m-1", data.feature_version)
    assert "target reached before stop" in prediction.meaning
    assert canonical_json(prediction.as_document())


def test_a_model_refuses_a_snapshot_from_another_feature_version():
    items = occurrences()
    data = build_dataset(items)
    model = fit(list(data.rows), list(data.labels), data.columns, params=FAST)
    with pytest.raises(FeatureLayoutMismatch, match="features-v9"):
        predict(model, replace(items[0].features, feature_version="features-v9"), model_version="m-1",
                feature_version=data.feature_version, label_version=data.label_version)


def test_a_model_refuses_a_layout_that_does_not_match_its_columns():
    items = occurrences()
    data = build_dataset(items)
    narrow = fit([row[:4] for row in data.rows], list(data.labels), data.columns[:4], params=FAST)
    with pytest.raises(FeatureLayoutMismatch, match="columns"):
        predict(narrow, items[0].features, model_version="m-1", feature_version=data.feature_version,
                label_version=data.label_version)
