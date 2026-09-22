"""Supersession schema, its append-only guarantees, and the scan that writes it (Plan 6, D96, D102)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from virtual_orders.research.service import run_research_scan
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


def seed_one_candidate(engine: Engine, detection_id: int) -> int:
    """Insert one bare setup candidate linked to `detection_id`, and return its id."""
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
                levels_errors=["NO_LEVELS"], client_signal_id="seed-signal", candidate_hash="seed",
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
