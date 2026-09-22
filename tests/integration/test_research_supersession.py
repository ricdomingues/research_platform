"""Supersession schema and its append-only guarantees (Plan 6, D96, D102)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from tests.integration.support import count
from virtual_orders.research.datasets import (
    ensure_dataset,
    open_revision,
    revision_as_of,
)
from virtual_orders.research.models import Timeframe
from virtual_orders.research.repository import (
    FACT_PATTERN_DETECTION,
    NO_LONGER_DETECTED,
    ResearchRunKind,
    active_detections,
    record_supersession,
    start_run,
)
from virtual_orders.storage import tables

DETECTION_END_TS = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)


def test_the_new_tables_exist_with_their_append_only_triggers(engine):
    with engine.connect() as conn:
        present = set(conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )).scalars())
    assert {"research_datasets", "research_dataset_revisions", "research_supersessions"} <= present


def test_a_supersession_can_never_be_updated_or_deleted(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO research_supersessions (fact_type, superseded_fact_id, replacement_fact_id, reason,"
            " source_run_id, superseded_at, input_content_hash)"
            " VALUES ('PATTERN_DETECTION', 1, NULL, 'NO_LONGER_DETECTED', gen_random_uuid(), now(), 'abc')"
        ))
    with pytest.raises(Exception, match="append-only|immutable|history"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE research_supersessions SET reason = 'SUPERSEDED_BY_REVISION'"))
    with pytest.raises(Exception, match="append-only|immutable|history"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM research_supersessions"))


def test_a_reason_outside_the_vocabulary_is_rejected(engine):
    with pytest.raises(Exception, match="check constraint|violates"):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO research_supersessions (fact_type, superseded_fact_id, replacement_fact_id,"
                " reason, source_run_id, superseded_at, input_content_hash)"
                " VALUES ('PATTERN_DETECTION', 1, NULL, 'BECAUSE_I_SAID_SO', gen_random_uuid(), now(), 'abc')"
            ))


def test_a_retraction_has_no_replacement_and_a_supersession_must_have_one(engine):
    """The two cases of D96 are distinguishable by the row itself, not by convention."""
    with pytest.raises(Exception, match="check constraint|violates"):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO research_supersessions (fact_type, superseded_fact_id, replacement_fact_id,"
                " reason, source_run_id, superseded_at, input_content_hash)"
                " VALUES ('PATTERN_DETECTION', 1, NULL, 'SUPERSEDED_BY_REVISION', gen_random_uuid(), now(), 'a')"
            ))


def test_the_fact_tables_carry_an_input_content_hash(engine):
    assert "input_content_hash" in tables.pattern_detections.c
    assert "input_content_hash" in tables.setup_candidates.c
    with engine.connect() as conn:
        columns = set(conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'pattern_detections'"
        )).scalars())
    assert "input_content_hash" in columns


DATASET = dict(
    name="OPERATIONAL_ALPACA_IEX_1M", family_version="v1", provider="alpaca", feed="iex",
    base_timeframe="1m", aggregation_version="timeframes-v1", calendar_version="nyse-v1",
)


def test_ensuring_a_dataset_twice_returns_the_same_identity(engine):
    with engine.begin() as conn:
        first = ensure_dataset(conn, **DATASET)
    with engine.begin() as conn:
        second = ensure_dataset(conn, **DATASET)
    assert first == second


def test_revisions_are_numbered_in_order_and_never_reused(engine):
    with engine.begin() as conn:
        dataset_id = ensure_dataset(conn, **DATASET)
        first = open_revision(conn, dataset_id=dataset_id,
                              data_as_of=datetime(2026, 9, 21, 20, 0, tzinfo=UTC), manifest_hash="aaa")
        second = open_revision(conn, dataset_id=dataset_id,
                               data_as_of=datetime(2026, 9, 22, 20, 0, tzinfo=UTC), manifest_hash="bbb")
    assert first != second
    with engine.connect() as conn:
        numbers = list(conn.execute(text(
            "SELECT revision_number FROM research_dataset_revisions ORDER BY revision_number"
        )).scalars())
    assert numbers == [1, 2]


def test_a_statistic_pinned_to_a_revision_still_resolves_after_a_later_one_exists(engine):
    """D102: a published number stays reproducible after the vendor corrects candles."""
    with engine.begin() as conn:
        dataset_id = ensure_dataset(conn, **DATASET)
        early = open_revision(conn, dataset_id=dataset_id,
                              data_as_of=datetime(2026, 9, 21, 20, 0, tzinfo=UTC), manifest_hash="aaa")
        open_revision(conn, dataset_id=dataset_id,
                      data_as_of=datetime(2026, 9, 22, 20, 0, tzinfo=UTC), manifest_hash="bbb")
    with engine.connect() as conn:
        resolved = revision_as_of(conn, dataset_id=dataset_id,
                                  as_of=datetime(2026, 9, 21, 23, 0, tzinfo=UTC))
    assert resolved == early


def test_concurrent_open_revision_is_serialized_by_advisory_lock(engine):
    """Two concurrent callers opening revisions for the same dataset both succeed with distinct numbers."""
    with engine.begin() as conn:
        dataset_id = ensure_dataset(conn, **DATASET)

    results = {}
    errors = {}
    barrier = threading.Barrier(2)  # synchronize threads to start at the same time

    def open_revision_from_thread(thread_id: int) -> None:
        try:
            barrier.wait()  # wait for both threads to be ready
            with engine.begin() as conn:
                revision_id = open_revision(
                    conn, dataset_id=dataset_id,
                    data_as_of=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
                    manifest_hash=f"hash_{thread_id}"
                )
                results[thread_id] = revision_id
        except Exception as e:
            errors[thread_id] = e

    thread1 = threading.Thread(target=open_revision_from_thread, args=(1,))
    thread2 = threading.Thread(target=open_revision_from_thread, args=(2,))
    thread1.start()
    thread2.start()
    thread1.join()
    thread2.join()

    # Both threads should succeed with no errors
    assert not errors, f"Unexpected errors: {errors}"
    assert len(results) == 2
    assert results[1] != results[2]

    # Verify revision numbers are 1 and 2 in the database
    with engine.connect() as conn:
        numbers = sorted(conn.execute(text(
            "SELECT revision_number FROM research_dataset_revisions WHERE dataset_id = :dataset_id"
            " ORDER BY revision_number"
        ), {"dataset_id": dataset_id}).scalars())
    assert numbers == [1, 2]


def seed_one_detection(engine: Engine) -> int:
    """Insert one bare pattern detection, independent of the full scan pipeline, and return its id."""
    with engine.begin() as conn:
        run = start_run(
            conn, kind=ResearchRunKind.RESEARCH_SCAN, data_as_of=DETECTION_END_TS, code_version="test-sha",
            engine_version="candles-v1", configuration={},
        )
        row = conn.execute(
            tables.pattern_detections.insert().values(
                run_id=run.run_id, ticker="AAPL", timeframe="15m", pattern="HAMMER", direction="BULLISH",
                start_ts=DETECTION_END_TS - timedelta(minutes=15), end_ts=DETECTION_END_TS, candles=1,
                geometry_score=Decimal("0.5"), context_score=Decimal("0.5"), overall_score=Decimal("0.5"),
                engine_version="candles-v1", price_source="test", evidence={}, evidence_hash="seed",
                data_as_of=DETECTION_END_TS, created_at=DETECTION_END_TS,
            ).returning(tables.pattern_detections.c.id)
        )
        return int(row.scalar_one())


def test_a_retracted_detection_leaves_the_active_view_but_stays_in_the_table(engine):
    detection_id = seed_one_detection(engine)          # helper below
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(),
            revision_id=None, superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC),
            input_content_hash="corrected",
        )
    with engine.connect() as conn:
        active = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                   end_ts=DETECTION_END_TS, engine_version="candles-v1")
    assert active == []
    assert count(engine, "pattern_detections") == 1   # the fact itself is untouched


def test_the_active_view_answers_as_of_an_earlier_instant(engine):
    """Reconstructing what the platform claimed before a correction is the point of D96."""
    detection_id = seed_one_detection(engine)
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(),
            revision_id=None, superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC),
            input_content_hash="corrected",
        )
    with engine.connect() as conn:
        before = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15, end_ts=DETECTION_END_TS,
                                   engine_version="candles-v1",
                                   as_of=datetime(2026, 9, 22, 18, 0, tzinfo=UTC))
    assert [row["id"] for row in before] == [detection_id]


def test_recording_the_same_supersession_twice_is_idempotent(engine):
    detection_id = seed_one_detection(engine)
    arguments = dict(
        fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id, replacement_fact_id=None,
        reason=NO_LONGER_DETECTED, source_run_id=uuid4(), revision_id=None,
        superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC), input_content_hash="corrected",
    )
    with engine.begin() as conn:
        assert record_supersession(conn, **arguments) is True
    with engine.begin() as conn:
        assert record_supersession(conn, **arguments) is False
    assert count(engine, "research_supersessions") == 1
