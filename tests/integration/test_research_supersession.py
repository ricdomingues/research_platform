"""Supersession schema and its append-only guarantees (Plan 6, D96, D102)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from virtual_orders.research.datasets import (
    ensure_dataset,
    open_revision,
    revision_as_of,
)
from virtual_orders.storage import tables


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
