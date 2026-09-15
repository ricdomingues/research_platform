"""Helpers for the observation read-model tests. Never a conftest (Plan 3B close-out entry 20)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from tests.integration.support import CODE_VERSION
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation_window import ObservationWindow, session_window


def recorded_run(
    engine: Engine,
    kind: RunKind,
    *,
    start_detail: Mapping[str, Any],
    status: RunStatus | None,
    detail: Mapping[str, Any] | None = None,
) -> UUID:
    """A run as its writer records it: the start detail, then (unless `status` is None) one final status."""
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, kind, as_of, CODE_VERSION, detail=start_detail)
        if status is not None:
            finish_run(conn, run.run_id, status, detail)
    return run.run_id


def window_for(engine: Engine, day: date) -> ObservationWindow:
    return session_window(day, acquire_data_as_of(engine))
