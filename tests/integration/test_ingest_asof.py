import threading
import time
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import text

from tests.integration.support import (
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    backdated_batch,
    count,
    flat_raw,
    raw,
)
from tests.support import et
from virtual_orders.marketdata.asof import acquire_data_as_of, read_bar_version, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.ingest import ingest_bars, store_batch
from virtual_orders.marketdata.sources import SourceDataError
from virtual_orders.storage.database import INGEST_LOCK_KEY

START, END = et(DAY, "10:00"), et(DAY, "10:05")


def read(engine, as_of, start=START, end=END):
    with engine.connect() as conn:
        return read_bars_as_of(conn, TICKER, PRICE_SOURCE, start, end, as_of)


def test_ingest_then_read_one_version_per_minute(engine):
    source = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100))
    result = ingest_bars(engine, source, TICKER, START, END)
    assert (result.fetched, result.stored) == (5, 5) and result.batch_id is not None
    bars = read(engine, acquire_data_as_of(engine))
    assert [b.ts for b in bars] == [et(DAY, f"10:0{i}") for i in range(5)]
    assert {b.batch_id for b in bars} == {result.batch_id}
    assert count(engine, "bar_batches") == 1


def test_identical_reingestion_creates_no_batch(engine):
    source = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100))
    ingest_bars(engine, source, TICKER, START, END)
    again = ingest_bars(engine, source, TICKER, START, END)
    assert (again.batch_id, again.stored) == (None, 0)
    assert count(engine, "bar_batches") == 1 and count(engine, "bars_1m") == 5


def test_vendor_correction_is_a_new_version_and_old_reads_are_stable(engine):
    source = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100))
    first = ingest_bars(engine, source, TICKER, START, END)
    before = acquire_data_as_of(engine)
    source.load([raw(DAY, "10:02", 100, 104, 99, 103)])
    corrected = ingest_bars(engine, source, TICKER, START, END)
    assert corrected.stored == 1

    old = {b.ts: b for b in read(engine, before)}
    new = {b.ts: b for b in read(engine, acquire_data_as_of(engine))}
    assert old[et(DAY, "10:02")].high == 100 and old[et(DAY, "10:02")].batch_id == first.batch_id
    assert new[et(DAY, "10:02")].high == 104 and new[et(DAY, "10:02")].batch_id == corrected.batch_id
    assert len(new) == 5
    with engine.connect() as conn:
        pinned = read_bar_version(conn, TICKER, PRICE_SOURCE, et(DAY, "10:02"), first.batch_id)
        assert pinned.high == 100
        with pytest.raises(LookupError):
            read_bar_version(conn, TICKER, PRICE_SOURCE, et(DAY, "10:02"), UUID(int=7))


def test_minute_delivered_later_stays_missing_for_earlier_as_of(engine):
    bars = flat_raw(DAY, "10:00", "10:05", 100)
    source = FakeBarSource([b for b in bars if b.ts != et(DAY, "10:03")])
    ingest_bars(engine, source, TICKER, START, END)
    before = acquire_data_as_of(engine)
    source.load(bars)
    ingest_bars(engine, source, TICKER, START, END)
    assert et(DAY, "10:03") not in {b.ts for b in read(engine, before)}
    assert et(DAY, "10:03") in {b.ts for b in read(engine, acquire_data_as_of(engine))}


def test_as_of_reads_are_isolated_by_source(engine):
    ingest_bars(engine, FakeBarSource(flat_raw(DAY, "10:00", "10:05", 100)), TICKER, START, END)
    other = FakeBarSource(flat_raw(DAY, "10:00", "10:05", 200), source="other_feed")
    assert ingest_bars(engine, other, TICKER, START, END).stored == 5  # dedup never crosses sources
    as_of = acquire_data_as_of(engine)
    assert {b.open for b in read(engine, as_of)} == {100}
    with engine.connect() as conn:
        assert {b.open for b in read_bars_as_of(conn, TICKER, "other_feed", START, END, as_of)} == {200}


def test_same_ingested_at_breaks_ties_by_batch_id(engine):
    at = et(DAY, "20:00")
    backdated_batch(engine, TICKER, [raw(DAY, "10:00", 1, 1, 1, 1)], at, batch_id=UUID(int=1))
    backdated_batch(engine, TICKER, [raw(DAY, "10:00", 2, 2, 2, 2)], at, batch_id=UUID(int=2))
    (only,) = read(engine, at)
    assert only.batch_id == UUID(int=2) and only.open == 2


def test_watermark_waits_for_in_flight_ingestion(engine):
    holder = engine.connect()
    transaction = holder.begin()
    holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": INGEST_LOCK_KEY})
    in_flight_at = holder.execute(text("SELECT clock_timestamp()")).scalar_one()
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(at=acquire_data_as_of(engine)))
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert result["at"] > in_flight_at


@pytest.mark.parametrize("bars, message", [
    ([raw(DAY, "10:00", 100, 99, 98, 100)], "invalid bar"),
    ([raw(DAY, "10:00", 100, 100, 100, 100), raw(DAY, "10:00", 100, 100, 100, 100)], "duplicate"),
    ([raw(DAY, "10:07", 100, 100, 100, 100)], "outside"),
    ([raw(DAY, "10:00", 100, 100, 100, 100, ticker="MSFT")], "ticker"),
])
def test_invalid_batches_are_rejected_without_writes(engine, bars, message):
    with pytest.raises(SourceDataError, match=message):
        store_batch(engine, FakeBarSource(), TICKER, START, END, bars)
    assert count(engine, "bar_batches") == 0


def test_calendar_window_is_padded_on_both_sides():
    earliest, latest = et(DAY, "08:00"), et("2025-12-01", "16:00")
    calendar = calendar_for_window(earliest, latest)
    days = [s.day for s in calendar.sessions]
    assert days[0] < date(2025, 11, 25) and days[-1] > date(2025, 12, 1)
    assert calendar.first_expected_minute_at_or_after(earliest) == et(DAY, "09:30")
    assert calendar_for_window(earliest, latest) is calendar
