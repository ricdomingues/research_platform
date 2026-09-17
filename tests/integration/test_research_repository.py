"""Research persistence against real Postgres: idempotency, append-only history and the model registry.

The idempotency guarantee is the one that matters operationally: the scan job is scheduled, it will re-run over
the same candles, and it must never accumulate duplicate observations.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from core.domain.models import Direction
from tests.integration.support import count
from tests.research.support import bullish_engulfing_after_decline, zigzag
from tests.support import et
from virtual_orders.research.backtest import build_occurrences, summarize, time_splits
from virtual_orders.research.ml.dataset import build_dataset
from virtual_orders.research.ml.model import Hyperparameters
from virtual_orders.research.ml.registry import (
    ModelNotFound,
    latest_model,
    list_models,
    load_model,
    model_exists,
    register_model,
)
from virtual_orders.research.ml.training import train_dataset
from virtual_orders.research.models import Timeframe
from virtual_orders.research.repository import (
    ResearchRunKind,
    ResearchRunStatus,
    count_detections,
    finish_run,
    get_run,
    json_safe,
    last_detection_end_ts,
    record_backtest,
    record_candidate,
    record_detection,
    start_run,
)
from virtual_orders.research.scoring import score_setup
from virtual_orders.research.setups import build_candidate
from virtual_orders.storage import tables

DATA_AS_OF = et("2025-11-25", "16:30")
CONFIG = {"thresholds": {"doji_body_ratio": Decimal("0.05")}, "timeframes": ["15m"]}


def open_run(conn, kind=ResearchRunKind.RESEARCH_SCAN):
    return start_run(conn, kind=kind, data_as_of=DATA_AS_OF, code_version="test-sha",
                     engine_version="candles-v1", configuration=CONFIG)


def one_detection():
    (occurrence,) = [
        item for item in build_occurrences(bullish_engulfing_after_decline(), ticker="AAPL",
                                           data_as_of=DATA_AS_OF)
        if item.pattern == "BULLISH_ENGULFING"
    ]
    return occurrence


def test_a_repeated_scan_at_the_same_watermark_inserts_nothing_new(engine):
    occurrence = one_detection()
    candidate = build_candidate(
        ticker="AAPL", detection=occurrence.detection, snapshot=occurrence.features,
        score=score_setup(occurrence.detection, occurrence.features, Direction.LONG), data_as_of=DATA_AS_OF,
    )
    first_ids = []
    for _ in range(2):  # the same scan, run twice, exactly as the scheduler will
        with engine.begin() as conn:
            run = open_run(conn)
            detection_id, inserted = record_detection(
                conn, run_id=run.run_id, ticker="AAPL", price_source="fake_feed",
                detection=occurrence.detection, data_as_of=DATA_AS_OF,
            )
            candidate_id, candidate_inserted = record_candidate(
                conn, run_id=run.run_id, detection_id=detection_id, candidate=candidate
            )
            finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED)
            first_ids.append((detection_id, inserted, candidate_id, candidate_inserted))

    assert first_ids[0][1] is True and first_ids[1][1] is False  # inserted, then recognised
    assert first_ids[0][3] is True and first_ids[1][3] is False
    assert first_ids[0][0] == first_ids[1][0]  # the second run points at the first run's row
    assert first_ids[0][2] == first_ids[1][2]
    assert count(engine, "pattern_detections") == 1
    assert count(engine, "setup_candidates") == 1
    assert count(engine, "research_runs") == 2  # both runs are recorded; only the observations are deduplicated


def test_a_corrected_reading_is_stored_beside_the_original(engine):
    occurrence = one_detection()
    corrected = replace(
        occurrence.detection,
        evidence={**occurrence.detection.evidence, "engulf_ratio": Decimal("1.9000")},
    )
    assert corrected.evidence_hash != occurrence.detection.evidence_hash
    with engine.begin() as conn:
        run = open_run(conn)
        for detection in (occurrence.detection, corrected):
            record_detection(conn, run_id=run.run_id, ticker="AAPL", price_source="fake_feed",
                             detection=detection, data_as_of=DATA_AS_OF)
    # Same pattern, same candle, different reading: append-only history keeps both, exactly as bars_1m keeps a
    # vendor correction as a new row rather than editing the old one.
    assert count(engine, "pattern_detections") == 2


@pytest.mark.parametrize("statement", [
    "UPDATE pattern_detections SET pattern = 'x'",
    "DELETE FROM pattern_detections",
    "TRUNCATE pattern_detections CASCADE",
])
def test_research_facts_are_append_only(engine, statement):
    occurrence = one_detection()
    with engine.begin() as conn:
        run = open_run(conn)
        record_detection(conn, run_id=run.run_id, ticker="AAPL", price_source="fake_feed",
                         detection=occurrence.detection, data_as_of=DATA_AS_OF)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement))


def test_the_app_role_can_insert_research_facts_but_never_change_them(engine):
    occurrence = one_detection()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE vo_app"))
        run = open_run(conn)
        record_detection(conn, run_id=run.run_id, ticker="AAPL", price_source="fake_feed",
                         detection=occurrence.detection, data_as_of=DATA_AS_OF)
    with pytest.raises(DBAPIError, match="permission denied"):
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE vo_app"))
            conn.execute(text("UPDATE pattern_detections SET pattern = 'x'"))


def test_a_run_opens_running_and_closes_with_its_counts(engine):
    occurrence = one_detection()
    with engine.begin() as conn:
        run = open_run(conn)
        stored = get_run(conn, run.run_id)
        assert stored is not None
        assert (stored["status"], stored["completed_at"]) == (ResearchRunStatus.RUNNING.value, None)
        assert stored["config_hash"] == run.config_hash
        record_detection(conn, run_id=run.run_id, ticker="AAPL", price_source="fake_feed",
                         detection=occurrence.detection, data_as_of=DATA_AS_OF)
        finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED,
                   {"tickers": ["AAPL"], "detections": count_detections(conn, run.run_id)})
        closed = get_run(conn, run.run_id)
    assert closed is not None
    assert closed["status"] == ResearchRunStatus.COMPLETED.value
    assert closed["completed_at"] is not None
    assert closed["detail"]["detections"] == 1
    assert closed["detail"]["configuration"]["timeframes"] == ["15m"]  # the opening detail is preserved


def test_a_failed_run_records_its_failure_without_touching_its_observations(engine):
    occurrence = one_detection()
    with engine.begin() as conn:
        run = open_run(conn)
        record_detection(conn, run_id=run.run_id, ticker="AAPL", price_source="fake_feed",
                         detection=occurrence.detection, data_as_of=DATA_AS_OF)
        finish_run(conn, run.run_id, ResearchRunStatus.FAILED, {"failures": {"MSFT": "ERROR:ValueError"}})
        stored = get_run(conn, run.run_id)
    assert stored is not None and stored["status"] == ResearchRunStatus.FAILED.value
    assert stored["detail"]["failures"] == {"MSFT": "ERROR:ValueError"}
    assert count(engine, "pattern_detections") == 1  # the observations it did make are still facts


def test_backtest_statistics_survive_the_storage_boundary(engine):
    occurrences = build_occurrences(zigzag(cycles=3), ticker="AAPL", data_as_of=DATA_AS_OF)
    stats = summarize(occurrences, horizon=10)
    assert isinstance(stats.max_drawdown_r, float)  # the statistic itself is a float, by design
    with engine.begin() as conn:
        run = open_run(conn, ResearchRunKind.BACKTEST)
        record_backtest(conn, run_id=run.run_id, stats=stats, pattern="THREE_WHITE_SOLDIERS",
                        timeframe=Timeframe.M15, split="ALL", period_from=DATA_AS_OF - timedelta(days=1),
                        period_to=DATA_AS_OF, data_as_of=DATA_AS_OF)
        finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED)
        row = conn.execute(select(tables.research_backtests)).mappings().one()
    assert row["samples"] == stats.samples and row["wins"] == stats.wins
    assert isinstance(row["max_drawdown_r"], Decimal)  # stored as an exact number, never a float
    assert row["ambiguity_policy"] == "STOP_FIRST_ON_SAME_CANDLE_V1"
    assert isinstance(row["statistics"]["win_rate_ci"][0], int | float | str)


def test_non_finite_statistics_are_stored_as_missing_rather_than_as_text():
    assert json_safe(float("nan")) is None
    assert json_safe(float("inf")) is None
    assert json_safe({"a": [1.5, True]}) == {"a": [Decimal("1.5"), True]}


def test_last_detection_end_ts_lets_a_scan_resume_where_it_stopped(engine):
    occurrence = one_detection()
    with engine.connect() as conn:
        assert last_detection_end_ts(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                     engine_version="candles-v1") is None
    with engine.begin() as conn:
        run = open_run(conn)
        record_detection(conn, run_id=run.run_id, ticker="AAPL", price_source="fake_feed",
                         detection=occurrence.detection, data_as_of=DATA_AS_OF)
    with engine.connect() as conn:
        assert last_detection_end_ts(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                     engine_version="candles-v1") == occurrence.detection.end_ts
        # A different engine version has scanned nothing: a rule change re-reads its own history.
        assert last_detection_end_ts(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                     engine_version="candles-v2") is None


def trained_model():
    occurrences = build_occurrences(zigzag(cycles=5), ticker="AAPL", data_as_of=DATA_AS_OF)
    train, validation, _ = time_splits(occurrences)
    return train_dataset(
        build_dataset(train.occurrences), validation=build_dataset(validation.occurrences),
        created_at=DATA_AS_OF, params=Hyperparameters(n_estimators=6, max_depth=2, min_samples_leaf=5),
    )


def test_the_model_registry_never_overwrites_a_version(engine):
    trained = trained_model()
    with engine.begin() as conn:
        run = open_run(conn, ResearchRunKind.TRAINING)
        assert register_model(conn, run_id=run.run_id, trained=trained, data_as_of=DATA_AS_OF) is True
        assert register_model(conn, run_id=run.run_id, trained=trained, data_as_of=DATA_AS_OF) is False
        finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED)
    assert count(engine, "research_models") == 1
    with engine.connect() as conn:
        loaded = load_model(conn, trained.model_version)
        assert loaded.artifact_hash == trained.artifact_hash
        assert loaded.feature_version == trained.feature_version
        # The artifact round-trips out of the database and still scores identically.
        rows = [row for row in build_dataset(
            build_occurrences(zigzag(cycles=2), ticker="AAPL", data_as_of=DATA_AS_OF)).rows]
        assert loaded.model.predict_proba(rows) == trained.model.predict_proba(rows)
        assert model_exists(conn, artifact_hash=trained.artifact_hash,
                            feature_version=trained.feature_version) is True
        assert latest_model(conn) is not None
        with pytest.raises(ModelNotFound):
            load_model(conn, "no-such-model")


def test_a_retrained_model_is_a_new_row_and_the_older_one_survives(engine):
    trained = trained_model()
    other = replace(trained, model_version=f"{trained.model_version}-b")
    with engine.begin() as conn:
        run = open_run(conn, ResearchRunKind.TRAINING)
        register_model(conn, run_id=run.run_id, trained=trained, data_as_of=DATA_AS_OF)
        register_model(conn, run_id=run.run_id, trained=other, data_as_of=DATA_AS_OF)
        finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED)
    assert count(engine, "research_models") == 2
    with engine.connect() as conn:
        assert load_model(conn, trained.model_version).model_version == trained.model_version
        assert len(list_models(conn)) == 2
        assert "artifact" not in list_models(conn)[0]  # the listing never carries the ensemble itself
