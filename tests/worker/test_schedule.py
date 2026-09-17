from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from virtual_orders.worker.schedule import (
    build_schedule,
    interval_config_errors,
    live_session,
    session_closed_today,
    session_opening_today,
    worker_lock_schedule,
)

ET = ZoneInfo("America/New_York")


def at(day: str, hm: str) -> datetime:
    year, month, dom = (int(part) for part in day.split("-"))
    hour, minute = (int(part) for part in hm.split(":"))
    return datetime(year, month, dom, hour, minute, tzinfo=ET)


def triggers(interval: int = 2):
    return {spec.job_id: spec.trigger for spec in build_schedule(interval)}


def next_fire(job: str, now: datetime, interval: int = 2) -> datetime:
    return triggers(interval)[job].get_next_fire_time(None, now)


def test_job_ids_are_fixed():
    assert list(triggers()) == ["live_cycle", "watchlist", "opening", "end_of_day", "health_watch",
                                "deliver_alerts", "research_scan"]


def test_research_scan_is_its_own_job_on_the_quarter_hour():
    """Plan 5 (D91): a separate job on its own cadence, so a scan can never delay order evaluation."""
    assert next_fire("research_scan", at("2025-11-24", "09:31")) == at("2025-11-24", "09:45")
    assert next_fire("research_scan", at("2025-11-24", "09:46")) == at("2025-11-24", "10:00")
    assert next_fire("research_scan", at("2025-11-24", "16:59")) == at("2025-11-25", "09:00")
    # The cron is calendar-blind, exactly like every other job here: it fires on Thanksgiving too, and the job
    # body is what consults the NYSE calendar and reports OUTSIDE_SESSION.
    assert next_fire("research_scan", at("2025-11-27", "11:00")) == at("2025-11-27", "11:00")


def test_watchlist_is_its_own_job_on_the_live_cadence():
    for now, expected in ((at("2025-11-24", "09:31"), at("2025-11-24", "09:32")),
                          (at("2025-11-24", "16:59"), at("2025-11-25", "09:00"))):
        assert next_fire("watchlist", now) == next_fire("live_cycle", now) == expected
    assert next_fire("watchlist", at("2025-11-24", "09:31"), interval=5) == at("2025-11-24", "09:35")


def test_worker_lock_is_checked_every_minute():
    spec = worker_lock_schedule()
    assert spec.job_id == "worker_lock" and spec.trigger.interval == timedelta(minutes=1)


def test_live_cycle_fires_every_interval_on_weekdays_between_9_and_16_et():
    assert next_fire("live_cycle", at("2025-11-24", "09:31")) == at("2025-11-24", "09:32")
    assert next_fire("live_cycle", at("2025-11-24", "09:31"), interval=5) == at("2025-11-24", "09:35")
    assert next_fire("live_cycle", at("2025-11-24", "16:59")) == at("2025-11-25", "09:00")
    assert next_fire("live_cycle", at("2025-11-28", "17:00")) == at("2025-12-01", "09:00")


def test_opening_fires_at_9_25_et_on_weekdays():
    assert next_fire("opening", at("2025-11-25", "09:26")) == at("2025-11-26", "09:25")
    assert next_fire("opening", at("2025-11-28", "10:00")) == at("2025-12-01", "09:25")


def test_end_of_day_fires_at_16_30_and_18_30_et():
    assert next_fire("end_of_day", at("2025-11-25", "16:31")) == at("2025-11-25", "18:30")
    assert next_fire("end_of_day", at("2025-11-25", "18:31")) == at("2025-11-26", "16:30")


def test_interval_jobs_use_the_configured_cadence():
    assert triggers(3)["health_watch"].interval == timedelta(minutes=3)
    assert triggers(3)["deliver_alerts"].interval == timedelta(minutes=1)


@pytest.mark.parametrize("interval", [0, 60])
def test_interval_outside_the_cron_minute_range_fails_at_startup(interval):
    assert interval_config_errors(interval) == ["OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"]
    with pytest.raises(ValueError, match="between 1 and 59"):
        build_schedule(interval)


@pytest.mark.parametrize("interval", [1, 2, 59])
def test_interval_inside_the_cron_minute_range_is_valid(interval):
    assert interval_config_errors(interval) == [] and len(build_schedule(interval)) == 7


def test_live_session_window_follows_the_nyse_calendar():
    assert live_session(at("2025-11-25", "09:29")) is None
    assert live_session(at("2025-11-25", "09:30")).day == date(2025, 11, 25)
    assert live_session(at("2025-11-25", "16:05")) is not None and live_session(at("2025-11-25", "16:06")) is None
    assert live_session(at("2025-11-28", "13:05")) is not None and live_session(at("2025-11-28", "13:06")) is None
    assert live_session(at("2025-11-27", "11:00")) is None  # Thanksgiving


def test_dst_spring_forward_transition_next_fire_and_live_session():
    """2026-03-08 (Sun) is the US spring-forward day; 2026-03-09 (Mon) is the first EDT session."""
    assert next_fire("live_cycle", at("2026-03-06", "16:59")) == at("2026-03-09", "09:00")
    assert next_fire("opening", at("2026-03-06", "09:26")) == at("2026-03-09", "09:25")
    assert next_fire("end_of_day", at("2026-03-06", "18:31")) == at("2026-03-09", "16:30")
    assert live_session(at("2026-03-09", "09:29")) is None
    assert live_session(at("2026-03-09", "09:30")).day == date(2026, 3, 9)
    assert live_session(at("2026-03-09", "16:05")) is not None
    assert live_session(at("2026-03-09", "16:06")) is None


def test_dst_fall_back_transition_next_fire_and_live_session():
    """2025-11-02 (Sun) is the US fall-back day; 2025-11-03 (Mon) is the first EST session."""
    assert next_fire("live_cycle", at("2025-10-31", "16:59")) == at("2025-11-03", "09:00")
    assert next_fire("opening", at("2025-10-31", "09:26")) == at("2025-11-03", "09:25")
    assert next_fire("end_of_day", at("2025-10-31", "18:31")) == at("2025-11-03", "16:30")
    assert live_session(at("2025-11-03", "09:29")) is None
    assert live_session(at("2025-11-03", "09:30")).day == date(2025, 11, 3)
    assert live_session(at("2025-11-03", "16:05")) is not None
    assert live_session(at("2025-11-03", "16:06")) is None


def test_opening_and_close_detection():
    assert session_opening_today(at("2025-11-26", "09:25")).day == date(2025, 11, 26)
    assert session_opening_today(at("2025-11-26", "09:30")) is None
    assert session_opening_today(at("2025-11-27", "09:25")) is None
    assert session_closed_today(at("2025-11-25", "16:30")).day == date(2025, 11, 25)
    assert session_closed_today(at("2025-11-25", "15:59")) is None
    assert session_closed_today(at("2025-11-28", "16:30")).day == date(2025, 11, 28)  # half day
