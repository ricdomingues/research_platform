"""Supersession schema, its append-only guarantees, and the scan that writes it (Plan 6, D96, D102)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from tests.integration.support import DAY, PRICE_SOURCE, backdated_batch, count
from tests.integration.test_research_scan import (
    CONFIG,
    MARKET_NOW,
    correct_one_minute,
    marching_bars,
    seed,
)
from tests.support import et
from virtual_orders.alerts.watchlist import add_ticker
from virtual_orders.marketdata.sources import RawBar
from virtual_orders.readmodels.research import candidate_detail, list_candidates, list_detections, pattern_markers
from virtual_orders.research.datasets import (
    ensure_dataset,
    open_revision,
    revision_as_of,
)
from virtual_orders.research.models import Timeframe
from virtual_orders.research.repository import (
    FACT_PATTERN_DETECTION,
    FACT_SETUP_CANDIDATE,
    NO_LONGER_DETECTED,
    SUPERSEDED_BY_REVISION,
    ResearchRunKind,
    active_detections,
    record_supersession,
    start_run,
)
from virtual_orders.research.service import ScanConfig, run_research_scan
from virtual_orders.storage import tables

DETECTION_END_TS = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)
WINDOW_START = DETECTION_END_TS - timedelta(minutes=15)
WINDOW_END = DETECTION_END_TS + timedelta(minutes=15)


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


def seed_one_detection(engine: Engine, *, end_ts: datetime = DETECTION_END_TS, pattern: str = "HAMMER") -> int:
    """Insert one bare pattern detection, independent of the full scan pipeline, and return its id.

    `end_ts` defaults to the module's single fixed bucket; a caller placing more than one detection in the
    same window (to exercise `pattern_markers`, which returns several rows at once) passes distinct values.
    """
    with engine.begin() as conn:
        run = start_run(
            conn, kind=ResearchRunKind.RESEARCH_SCAN, data_as_of=end_ts, code_version="test-sha",
            engine_version="candles-v1", configuration={},
        )
        row = conn.execute(
            tables.pattern_detections.insert().values(
                run_id=run.run_id, ticker="AAPL", timeframe="15m", pattern=pattern, direction="BULLISH",
                start_ts=end_ts - timedelta(minutes=15), end_ts=end_ts, candles=1,
                geometry_score=Decimal("0.5"), context_score=Decimal("0.5"), overall_score=Decimal("0.5"),
                engine_version="candles-v1", price_source="test", evidence={}, evidence_hash="seed",
                data_as_of=end_ts, created_at=end_ts,
            ).returning(tables.pattern_detections.c.id)
        )
        return int(row.scalar_one())


def seed_one_candidate(engine: Engine, detection_id: int) -> int:
    """Insert one bare setup candidate linked to `detection_id`, and return its id.

    `candidate_hash` is derived from `detection_id` so that seeding a candidate for more than one detection in
    the same test never collides against `setup_candidates`'s own uniqueness constraint.
    """
    with engine.begin() as conn:
        run = start_run(
            conn, kind=ResearchRunKind.RESEARCH_SCAN, data_as_of=DETECTION_END_TS, code_version="test-sha",
            engine_version="candles-v1", configuration={},
        )
        row = conn.execute(
            tables.setup_candidates.insert().values(
                run_id=run.run_id, pattern_detection_id=detection_id, ticker="AAPL", timeframe="15m",
                detected_at=DETECTION_END_TS, direction="LONG", pattern="HAMMER", strategy="test-strategy",
                strategy_version="v1", feature_version="v1", scoring_version="v1", feature_document={},
                thesis_document={}, deterministic_score=Decimal("0.5"), ml_probability=None,
                model_version=None, label_version=None, entry_zone_low=None, entry_zone_high=None,
                stop=None, target1=None, target2=None, risk_reward=None, levels_valid=False,
                levels_errors=["NO_LEVELS"], client_signal_id=f"seed-signal-{detection_id}",
                candidate_hash=f"seed-{detection_id}",
                data_as_of=DETECTION_END_TS, created_at=DETECTION_END_TS,
            ).returning(tables.setup_candidates.c.id)
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


# --- The scan's own reconciliation, end to end over real Postgres (Plan 6, D96) ---------------------------
#
# Both cases of D96 in one scan, because they are two different claims about a correction and each has its own
# way of being wrong. Case A is a corrected candle that still reads as the same pattern, so the stale row is
# superseded *by* a named replacement; case B is a corrected candle that no longer reads as anything, so the
# stale row is retracted with nothing to point at. Only case B had coverage before this test.

# The 15-minute bucket whose own minutes carry each correction. Minute 239 closes the bucket ending 13:29 ET
# and minute 269 the bucket ending 13:59 ET; a correction anywhere inside a bucket changes that bucket's
# input identity, which is what makes the scan look at its stored readings again.
CASE_A_MINUTE = 239
CASE_B_MINUTE = 269


def reverse_one_minute(engine, ticker="AAPL", minute_index=CASE_B_MINUTE, drop="1.00"):
    """A vendor correction that turns a rising minute into a sharp fall.

    The bucket it closes was a bullish candle marching with its two neighbours — a Three White Soldiers by the
    engine's own rule. Corrected, the bucket closes below its own open, so that reading is simply gone: the
    stored fact has no successor, which is exactly case B of D96.
    """
    original = marching_bars(ticker)[minute_index]
    close = original.close - Decimal(drop)
    corrected = RawBar(
        ticker, original.ts, original.open, max(original.open, original.high),
        close - Decimal("0.01"), close, original.volume,
    )
    backdated_batch(engine, ticker, [corrected], ingested_at=et(DAY, "16:40"))


def supersession_rows(engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(
            "SELECT s.reason, s.superseded_fact_id, s.replacement_fact_id,"
            "       stale.pattern AS stale_pattern, stale.end_ts AS stale_end_ts,"
            "       fresh.pattern AS fresh_pattern, fresh.end_ts AS fresh_end_ts,"
            "       fresh.evidence_hash AS fresh_evidence_hash"
            " FROM research_supersessions s"
            " JOIN pattern_detections stale ON stale.id = s.superseded_fact_id"
            " LEFT JOIN pattern_detections fresh ON fresh.id = s.replacement_fact_id"
            " ORDER BY s.id"
        )).mappings()]


def test_a_correction_supersedes_the_reading_it_invalidates(engine):
    """The whole point of D96, end to end over real Postgres: both cases, told apart by the rows themselves."""
    seed(engine)
    first = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                              market_now=MARKET_NOW, config=CONFIG)
    assert first.detections > 0
    assert first.supersessions == 0  # nothing was stored before it, so there is nothing to reconcile against
    stored_before = count(engine, "pattern_detections")

    correct_one_minute(engine, minute_index=CASE_A_MINUTE)   # case A: the same pattern, read differently
    reverse_one_minute(engine)                               # case B: the pattern is gone
    second = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=et(DAY, "16:45"), config=CONFIG)

    assert second.supersessions >= 2
    assert count(engine, "pattern_detections") >= stored_before  # nothing was deleted or rewritten
    rows = supersession_rows(engine)
    assert len(rows) == second.supersessions

    replaced = [row for row in rows if row["reason"] == SUPERSEDED_BY_REVISION]
    retracted = [row for row in rows if row["reason"] == NO_LONGER_DETECTED]
    assert replaced, "case A never happened: a corrected candle that still reads as its pattern"
    assert retracted, "case B never happened: a corrected candle that reads as nothing"

    for row in replaced:
        # The replacement must be a real, distinct detection of the same claim at the same bucket. A link that
        # resolves to nothing is the failure mode of writing the supersession before the new reading is stored.
        assert row["replacement_fact_id"] is not None
        assert row["replacement_fact_id"] != row["superseded_fact_id"]
        assert row["fresh_pattern"] == row["stale_pattern"]
        assert row["fresh_end_ts"] == row["stale_end_ts"]
        assert row["fresh_evidence_hash"] is not None
    for row in retracted:
        assert row["replacement_fact_id"] is None

    with engine.connect() as conn:
        still_active = active_detections(
            conn, ticker="AAPL", timeframe=Timeframe.M15, end_ts=retracted[0]["stale_end_ts"],
            engine_version="candles-v1",
        )
    # The retracted reading left the active view without leaving the table.
    assert retracted[0]["superseded_fact_id"] not in {row["id"] for row in still_active}


# The reading that `correct_one_minute`'s own default corrects. Minute 209 closes the bucket ending 12:59 ET,
# which carries no reading of its own; the reading it changes is the three-candle pattern ending 13:29 ET, two
# buckets later. A bucket's readings are not a function of its own bars alone.
CHANGED_BUCKET_END = "13:29"


def test_a_correction_to_an_earlier_candle_of_a_pattern_supersedes_the_reading_it_changes(engine):
    """A three-candle reading changes when any of its three candles does, not only its last one.

    Reading the bucket's own bars alone let the corrected reading be stored while the stale one stayed active,
    so the active view answered a single bucket with two contradictory readings and any statistic over it
    counted that occurrence twice. That is worse than the stale row this phase set out to retire.
    """
    seed(engine)
    run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                      market_now=MARKET_NOW, config=CONFIG)
    with engine.connect() as conn:
        before = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                   end_ts=et(DAY, CHANGED_BUCKET_END), engine_version="candles-v1")
    assert len(before) == 1  # one reading for this bucket to begin with

    correct_one_minute(engine)  # the default: a minute two buckets before the reading it changes
    run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                      market_now=et(DAY, "16:45"), config=CONFIG)

    with engine.connect() as conn:
        after = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                  end_ts=et(DAY, CHANGED_BUCKET_END), engine_version="candles-v1")
    assert len(after) == 1, "the active view holds more than one reading for a single bucket"
    assert after[0]["pattern"] == before[0]["pattern"]
    assert after[0]["id"] != before[0]["id"], "the corrected reading, not the stale one, is what stayed active"

    rows = supersession_rows(engine)
    assert [row["reason"] for row in rows] == [SUPERSEDED_BY_REVISION]
    assert rows[0]["superseded_fact_id"] == before[0]["id"]
    assert rows[0]["replacement_fact_id"] == after[0]["id"]


# A reading's `evidence_hash` carries its prior trend (`candlesticks.py` splices `prior.evidence()` into the
# detection's evidence, and `context_score` is a function of the same `PriorTrend`), and the prior trend of a
# three-candle reading at bucket N is measured over buckets N-22 through N-3. Correcting the close of bucket
# N-22 therefore changes what the reading at N says, while bucket N's own bars — and the two before it — are
# untouched.
#
# The trend is the move between the FIRST and LAST candle of its lookback, so those two buckets are the only
# distances at which a one-minute correction can change it: N-3, which a four-bucket window would already
# catch, and N-22, which pins the whole span. This test uses N-22 deliberately.
PRIOR_WINDOW_FIRST_MINUTE = 14   # closes bucket 0, the first candle of the lookback behind the reading below
READING_AFTER_PRIOR_BUCKET_END = "15:14"  # ET; bucket 22, whose prior trend is measured over buckets 0..19


def restate_one_minute(engine, ticker="AAPL", minute_index=PRIOR_WINDOW_FIRST_MINUTE, close="110.00"):
    """A vendor correction that moves a minute's close far enough to reverse the trend it anchors.

    The bucket this minute closes is the first of the prior-trend lookback behind a reading 22 buckets later.
    Restating it turns the measured move from a fall into a rise, so that reading's context score and its
    spliced prior-trend evidence both change — without touching a single bar of the reading's own candles.
    """
    original = marching_bars(ticker)[minute_index]
    corrected_close = Decimal(close)
    corrected = RawBar(
        ticker, original.ts, original.open, max(original.open, original.high),
        min(original.low, corrected_close - Decimal("0.01")), corrected_close, original.volume,
    )
    backdated_batch(engine, ticker, [corrected], ingested_at=et(DAY, "16:40"))


def test_a_correction_inside_the_prior_trend_supersedes_the_reading_whose_context_it_changes(engine):
    """`evidence_hash` covers the prior trend, so the window that decides "did the input change" must too.

    A reading is not a function of its pattern's candles alone: its context score and its prior-trend evidence
    are measured over the twenty buckets before them. Hashing only the pattern's own candles let a correction
    in that band change the reading while the guard called the input unchanged — and because the corrected
    reading is stored regardless, the bucket was left with two contradictory active readings and no link.
    """
    seed(engine)
    run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                      market_now=MARKET_NOW, config=CONFIG)
    bucket_end = et(DAY, READING_AFTER_PRIOR_BUCKET_END)
    with engine.connect() as conn:
        before = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                   end_ts=bucket_end, engine_version="candles-v1")
    assert len(before) == 1  # one reading for this bucket to begin with

    restate_one_minute(engine)
    run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                      market_now=et(DAY, "16:45"), config=CONFIG)

    with engine.connect() as conn:
        after = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                  end_ts=bucket_end, engine_version="candles-v1")
    assert len(after) == 1, "the active view holds more than one reading for a single bucket"
    assert after[0]["id"] != before[0]["id"], "the reading really did change, so the stale one must not remain"

    links = [row for row in supersession_rows(engine)
             if row["superseded_fact_id"] == before[0]["id"]]
    assert [row["reason"] for row in links] == [SUPERSEDED_BY_REVISION]
    assert links[0]["replacement_fact_id"] == after[0]["id"]


def test_re_ingesting_the_same_bars_under_a_new_batch_is_not_a_correction(engine):
    """D96 again, from the other side: provenance changed and the data did not, so nothing is reconciled."""
    seed(engine)
    run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                      market_now=MARKET_NOW, config=CONFIG)
    backdated_batch(engine, "AAPL", marching_bars("AAPL"), ingested_at=et(DAY, "16:40"))
    again = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                              market_now=et(DAY, "16:45"), config=CONFIG)
    assert again.supersessions == 0
    assert count(engine, "research_supersessions") == 0


# --- The active view reaches the read models (D96, task 7) ------------------------------------------------
#
# Reconciliation only ever writes a PATTERN_DETECTION-typed supersession row today (see the scan above): a
# candidate is derived from its detection, so a retracted detection must take its candidate with it even
# though no SETUP_CANDIDATE-typed row exists yet. The tests below cover both routes a candidate can be
# excluded by, plus the audit path that must still see everything.


def test_a_retracted_detection_disappears_from_the_read_models(engine):
    detection_id = seed_one_detection(engine)
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(), revision_id=None,
            superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC), input_content_hash="corrected",
        )
    with engine.connect() as conn:
        assert list_detections(conn, ticker="AAPL", limit=50) == []
        assert pattern_markers(conn, ticker="AAPL", timeframe=Timeframe.M15,
                               start=WINDOW_START, end=WINDOW_END) == []
        audited = list_detections(conn, ticker="AAPL", limit=50, include_superseded=True)
    assert [row["id"] for row in audited] == [detection_id]


def test_a_candidate_disappears_when_its_own_fact_is_superseded(engine):
    """The fact_type = SETUP_CANDIDATE path, kept working for the day reconciliation writes it directly."""
    detection_id = seed_one_detection(engine)
    candidate_id = seed_one_candidate(engine, detection_id)
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_SETUP_CANDIDATE, superseded_fact_id=candidate_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(), revision_id=None,
            superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC), input_content_hash="corrected",
        )
    with engine.connect() as conn:
        assert list_candidates(conn, ticker="AAPL", limit=50) == []
        assert candidate_detail(conn, candidate_id) is None
        audited = list_candidates(conn, ticker="AAPL", limit=50, include_superseded=True)
    assert [row["id"] for row in audited] == [candidate_id]


def test_a_candidate_disappears_when_its_parent_detection_is_retracted(engine):
    """Nothing writes a SETUP_CANDIDATE-typed row for this case, so the exclusion must follow the parent link."""
    detection_id = seed_one_detection(engine)
    candidate_id = seed_one_candidate(engine, detection_id)
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(), revision_id=None,
            superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC), input_content_hash="corrected",
        )
    with engine.connect() as conn:
        assert list_candidates(conn, ticker="AAPL", limit=50) == []
        assert candidate_detail(conn, candidate_id) is None
        audited = list_candidates(conn, ticker="AAPL", limit=50, include_superseded=True)
    assert [row["id"] for row in audited] == [candidate_id]


def test_pattern_markers_drops_a_superseded_candidates_fields_but_keeps_the_marker(engine):
    """The candidate-derived join side of `pattern_markers` (fix round 1 review finding).

    Three markers share one window: one whose candidate was superseded directly (its own SETUP_CANDIDATE row,
    since nothing writes those in production yet, but a later phase will), one whose detection never had a
    candidate at all, and one whose candidate is untouched. The middle case is the NULL trap: `setup_candidates
    .c.id` is NULL for a detection with no candidate, and `NULL NOT IN (...)` is NULL rather than TRUE, so a
    naive WHERE-clause guard would drop that marker's whole row instead of only its (already-absent) candidate
    fields. A superseded candidate must land in exactly the same shape as no candidate at all: the detection's
    own reading is unaffected and its marker stays, only the stale candidate fields go back to NULL.
    """
    superseded_detection = seed_one_detection(engine, end_ts=DETECTION_END_TS - timedelta(minutes=10),
                                              pattern="HAMMER")
    superseded_candidate = seed_one_candidate(engine, superseded_detection)
    candidateless_detection = seed_one_detection(engine, end_ts=DETECTION_END_TS, pattern="DOJI")
    active_detection = seed_one_detection(engine, end_ts=DETECTION_END_TS + timedelta(minutes=10),
                                          pattern="ENGULFING")
    active_candidate = seed_one_candidate(engine, active_detection)
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_SETUP_CANDIDATE, superseded_fact_id=superseded_candidate,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(), revision_id=None,
            superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC), input_content_hash="corrected",
        )

    with engine.connect() as conn:
        markers = pattern_markers(conn, ticker="AAPL", timeframe=Timeframe.M15, start=WINDOW_START, end=WINDOW_END)
    by_detection = {row["seq"]: row for row in markers}

    # All three detections still show a marker: the superseded candidate did not take its detection with it,
    # and the candidateless detection was not dropped by the guard against the superseded one.
    assert set(by_detection) == {superseded_detection, candidateless_detection, active_detection}

    # The superseded candidate's own fields are gone, exactly like a detection with no candidate at all.
    assert by_detection[superseded_detection]["candidate_id"] is None
    assert by_detection[superseded_detection]["deterministic_score"] is None
    assert by_detection[superseded_detection]["ml_probability"] is None

    # A detection that never had a candidate keeps the same NULL shape — the guard must not disturb this row.
    assert by_detection[candidateless_detection]["candidate_id"] is None
    assert by_detection[candidateless_detection]["deterministic_score"] is None

    # An untouched candidate keeps its own fields intact.
    assert by_detection[active_detection]["candidate_id"] == active_candidate
    assert by_detection[active_detection]["deterministic_score"] == Decimal("0.5")

    with engine.connect() as conn:
        audited = pattern_markers(conn, ticker="AAPL", timeframe=Timeframe.M15, start=WINDOW_START,
                                  end=WINDOW_END, include_superseded=True)
    audited_by_detection = {row["seq"]: row for row in audited}
    assert set(audited_by_detection) == {superseded_detection, candidateless_detection, active_detection}
    assert audited_by_detection[superseded_detection]["candidate_id"] == superseded_candidate
    assert audited_by_detection[superseded_detection]["deterministic_score"] == Decimal("0.5")


# --- The canary case becomes a portable regression fixture (task 8) -----------------------------------------
#
# Seven real rows from the Plan 5 canary: a research scan read candles whose bars were still arriving, so it
# detected a pattern from incomplete data; the bars landed afterwards, the candles changed shape, and those
# readings no longer hold. They were frozen in the table because the scan's resume watermark had already moved
# past them, which is exactly the class of row this phase exists to retract. The fixture carries the stored
# detection and the corrected bars as data -- never a database id -- so the case survives a fresh database, a
# restore, a migration and a different host.
#
# `input_content_hash` is seeded NULL because that is exactly how the real rows read: they predate the column,
# and that is what makes them exercise the "identity unknown, so reconcile" path rather than the fast
# "unchanged" skip.
#
# Each case ships its own `lookback_sessions`. The change-detection hash a revisited bucket is judged against
# covers `MAX_PATTERN_CANDLES + PRIOR_TREND_LOOKBACK` = 23 candles of the case's OWN timeframe ending at its
# bucket, and `run_research_scan`'s window only resamples the sessions it is told to look back across. Id 41
# is a 1h detection -- a session yields barely 6-7 hourly buckets, so 23 of them reach back four sessions --
# and four of the six 15m cases land early enough in their own session that the window reaches across the
# weekend into the prior one. Each case's `note` records the exact reasoning; see the task report for the span
# each case's `corrected_bars` was exported over.

FIXTURE = Path(__file__).parents[1] / "fixtures" / "research" / "canary_retraction.json"


def load_canary_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def insert_detection(engine: Engine, detection: dict) -> int:
    """Replay the stored fact exactly as the pre-fix engine wrote it, ids assigned by this database.

    `pattern_detections.run_id` is a foreign key into `research_runs` (migration 0006, predating this plan),
    so the stale row needs a real run to point at -- `uuid4()` alone is rejected. `start_run` gives it one,
    exactly as `seed_one_detection` above does for the same reason.
    """
    with engine.begin() as conn:
        run = start_run(
            conn, kind=ResearchRunKind.RESEARCH_SCAN, data_as_of=parse(detection["end_ts"]),
            code_version="test-sha", engine_version=detection["engine_version"], configuration={},
        )
        return int(conn.execute(tables.pattern_detections.insert().values(
            run_id=run.run_id, ticker=detection["ticker"], timeframe=detection["timeframe"],
            pattern=detection["pattern"], direction=detection["direction"],
            start_ts=parse(detection["start_ts"]), end_ts=parse(detection["end_ts"]),
            candles=detection["candles"], geometry_score=Decimal("0.5"), context_score=Decimal("0.5"),
            overall_score=Decimal("0.5"), engine_version=detection["engine_version"],
            price_source=PRICE_SOURCE, evidence=detection["evidence"],
            evidence_hash=detection["evidence_hash"], input_content_hash=None,
            data_as_of=parse(detection["end_ts"]), created_at=parse(detection["end_ts"]),
        ).returning(tables.pattern_detections.c.id)).scalar_one())


def store_bars(engine: Engine, ticker: str, bars: list[dict]) -> None:
    """The corrected minutes, written through the same backdated path the other integration tests use."""
    backdated_batch(engine, ticker, [
        RawBar(ticker, parse(bar["ts"]), Decimal(str(bar["open"])), Decimal(str(bar["high"])),
               Decimal(str(bar["low"])), Decimal(str(bar["close"])), Decimal(str(bar["volume"])))
        for bar in bars
    ], ingested_at=parse(bars[-1]["ts"]))
    with engine.begin() as conn:
        add_ticker(conn, ticker, added_at=parse(bars[0]["ts"]))


def market_now_for(case: dict) -> datetime:
    """Late enough that the bucket is settled and still inside the revisit window."""
    return parse(case["detection"]["end_ts"]) + timedelta(minutes=30)


CANARY_CASES = load_canary_cases()


@pytest.mark.parametrize("case", CANARY_CASES, ids=[case["case_id"] for case in CANARY_CASES])
def test_the_canary_cases_retract_against_their_corrected_bars(engine: Engine, case: dict) -> None:
    """Real regressions: incomplete data produced a reading, corrected data no longer supports it.

    Ids are deliberately absent from the fixture -- these must survive a fresh database, a restore and a
    different host. Each case is parametrized rather than looped over a single shared database: several of
    the seven cases' 23-candle identity windows overlap in wall-clock time (they are all AAPL, and four of
    them share the same trading day), so sharing one connection across cases would let one case's corrected
    bars and stored detections leak into another's resume watermark and revisit window. Parametrizing gives
    each case the fresh database the `engine` fixture already provides per test, which is what an independent
    regression case requires anyway.

    A per-case `ScanConfig` is required, not the module's `CONFIG`: case 41 is a 1h detection that `CONFIG`'s
    15m-only timeframe would never examine -- passing by never being looked at, which is exactly the vacuous
    test this fixture exists to prevent -- and `lookback_sessions` must reach back as far as each case's own
    identity window does (see the fixture's own `lookback_sessions` and `note` per case).
    """
    detection = case["detection"]
    timeframe = Timeframe(detection["timeframe"])
    config = ScanConfig(timeframes=(timeframe,), lookback_sessions=case["lookback_sessions"])

    detection_id = insert_detection(engine, detection)
    store_bars(engine, detection["ticker"], case["corrected_bars"])
    report = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=market_now_for(case), config=config)

    assert report.supersessions >= 1
    with engine.connect() as conn:
        active = active_detections(
            conn, ticker=detection["ticker"], timeframe=timeframe,
            end_ts=parse(detection["end_ts"]), engine_version=detection["engine_version"],
        )
    assert detection_id not in {row["id"] for row in active}
    assert count(engine, "pattern_detections") >= 1  # nothing was deleted
