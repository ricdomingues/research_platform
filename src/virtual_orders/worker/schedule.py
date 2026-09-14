"""Worker schedule (spec 5.3, D20): cron triggers in America/New_York; each job re-checks the NYSE calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.domain.calendar import Session
from virtual_orders.marketdata.calendars import calendar_for_window

MARKET_TZ = ZoneInfo("America/New_York")
LIVE_WINDOW_GRACE = timedelta(minutes=5)  # spec 5.3: 09:30-16:05 ET
MISFIRE_GRACE_SECONDS = 60
MIN_EVAL_INTERVAL_MINUTES, MAX_EVAL_INTERVAL_MINUTES = 1, 59  # D36: the cron minute field
LIVE_CYCLE = "live_cycle"
WATCHLIST = "watchlist"
OPENING = "opening"
END_OF_DAY = "end_of_day"
HEALTH_WATCH = "health_watch"
DELIVER_ALERTS = "deliver_alerts"
WORKER_LOCK = "worker_lock"


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    trigger: Any


def interval_config_errors(eval_interval_minutes: int) -> list[str]:
    """D36: CONFIG_INVALID codes for the worker schedule; names the variable, never its value."""
    if MIN_EVAL_INTERVAL_MINUTES <= eval_interval_minutes <= MAX_EVAL_INTERVAL_MINUTES:
        return []
    return ["OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"]


def build_schedule(eval_interval_minutes: int) -> tuple[JobSpec, ...]:
    if interval_config_errors(eval_interval_minutes):  # the CLI reports this first; this is the second barrier
        raise ValueError("EVAL_INTERVAL_MINUTES must be between 1 and 59 for the worker schedule")
    every = f"*/{eval_interval_minutes}"
    return (
        JobSpec(LIVE_CYCLE, CronTrigger(day_of_week="mon-fri", hour="9-16", minute=every, timezone=MARKET_TZ)),
        # D26: same cadence, separate job, so watchlist provider latency never delays or skips order evaluation.
        JobSpec(WATCHLIST, CronTrigger(day_of_week="mon-fri", hour="9-16", minute=every, timezone=MARKET_TZ)),
        JobSpec(OPENING, CronTrigger(day_of_week="mon-fri", hour=9, minute=25, timezone=MARKET_TZ)),
        JobSpec(END_OF_DAY, CronTrigger(day_of_week="mon-fri", hour="16,18", minute=30, timezone=MARKET_TZ)),
        JobSpec(HEALTH_WATCH, IntervalTrigger(minutes=eval_interval_minutes, timezone=MARKET_TZ)),
        JobSpec(DELIVER_ALERTS, IntervalTrigger(minutes=1, timezone=MARKET_TZ)),
    )


def worker_lock_schedule() -> JobSpec:
    """D39: registered by the runner, which owns the connection holding the lock."""
    return JobSpec(WORKER_LOCK, IntervalTrigger(minutes=1, timezone=MARKET_TZ))


def _sessions(now: datetime) -> tuple[Session, ...]:
    return calendar_for_window(now, now).sessions


def live_session(now: datetime) -> Session | None:
    """The session whose [open, close + 5 min] contains `now` (half days close early; holidays have none)."""
    return next((s for s in _sessions(now) if s.open_utc <= now <= s.close_utc + LIVE_WINDOW_GRACE), None)


def session_opening_today(now: datetime) -> Session | None:
    today = now.astimezone(MARKET_TZ).date()
    return next((s for s in _sessions(now) if s.day == today and now < s.open_utc), None)


def session_closed_today(now: datetime) -> Session | None:
    today = now.astimezone(MARKET_TZ).date()
    return next((s for s in _sessions(now) if s.day == today and s.close_utc <= now), None)
