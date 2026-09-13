"""Evaluation facts (spec 3.1, 3.6; D2): runs, append-only run status and per-order segments."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, select, text

from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import evaluation_run_status, evaluation_runs, order_eval_segments


class RunKind(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    ACTIONABILITY = "ACTIONABILITY"
    END_OF_DAY = "END_OF_DAY"
    OPENING = "OPENING"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RunInfo:
    run_id: UUID
    kind: RunKind
    data_as_of: datetime
    code_version: str
    started_at: datetime


@dataclass(frozen=True)
class SegmentRow:
    order_id: UUID
    run_id: UUID
    bar_from: datetime
    bar_to: datetime
    selected_data_hash: str
    first_seq: int
    event_count: int


def start_run(
    conn: Connection,
    kind: RunKind,
    data_as_of: datetime,
    code_version: str,
    started_at: datetime | None = None,
    detail: Mapping[str, Any] | None = None,
) -> RunInfo:
    started: datetime = started_at or conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    run = RunInfo(uuid4(), kind, data_as_of, code_version, started)
    conn.execute(evaluation_runs.insert().values(
        run_id=run.run_id, kind=kind.value, data_as_of=data_as_of, code_version=code_version, started_at=started,
    ))
    conn.execute(evaluation_run_status.insert().values(
        run_id=run.run_id, status=RunStatus.RUNNING.value, detail=to_document(dict(detail or {})),
        recorded_at=func.clock_timestamp(),
    ))
    return run


def finish_run(conn: Connection, run_id: UUID, status: RunStatus, detail: Mapping[str, Any] | None = None) -> None:
    conn.execute(evaluation_run_status.insert().values(
        run_id=run_id, status=status.value, detail=to_document(dict(detail or {})),
        recorded_at=func.clock_timestamp(),
    ))


def get_run(conn: Connection, run_id: UUID) -> RunInfo:
    row = conn.execute(select(evaluation_runs).where(evaluation_runs.c.run_id == run_id)).one()
    return RunInfo(row.run_id, RunKind(row.kind), row.data_as_of, row.code_version, row.started_at)


def latest_run_status(conn: Connection, run_id: UUID) -> tuple[RunStatus, dict[str, Any]]:
    row = conn.execute(
        select(evaluation_run_status.c.status, evaluation_run_status.c.detail)
        .where(evaluation_run_status.c.run_id == run_id)
        .order_by(evaluation_run_status.c.id.desc())
        .limit(1)
    ).one()
    return RunStatus(row.status), dict(row.detail)


def record_segment(conn: Connection, segment: SegmentRow) -> None:
    conn.execute(order_eval_segments.insert().values(
        order_id=segment.order_id, run_id=segment.run_id, bar_from=segment.bar_from, bar_to=segment.bar_to,
        selected_data_hash=segment.selected_data_hash, first_seq=segment.first_seq,
        event_count=segment.event_count,
    ))


def _segment(row: Any) -> SegmentRow:
    return SegmentRow(row.order_id, row.run_id, row.bar_from, row.bar_to, row.selected_data_hash,
                      row.first_seq, row.event_count)


def list_segments(conn: Connection, order_id: UUID) -> list[SegmentRow]:
    rows = conn.execute(
        select(order_eval_segments)
        .where(order_eval_segments.c.order_id == order_id)
        .order_by(order_eval_segments.c.first_seq, order_eval_segments.c.bar_from)
    ).all()
    return [_segment(row) for row in rows]


def last_segment_end(conn: Connection, order_id: UUID) -> datetime | None:
    value: datetime | None = conn.execute(
        select(func.max(order_eval_segments.c.bar_to)).where(order_eval_segments.c.order_id == order_id)
    ).scalar_one()
    return value


def segment_containing(conn: Connection, order_id: UUID, ts: datetime) -> SegmentRow | None:
    row = conn.execute(
        select(order_eval_segments).where(
            order_eval_segments.c.order_id == order_id,
            order_eval_segments.c.bar_from <= ts,
            order_eval_segments.c.bar_to >= ts,
        )
    ).first()
    return None if row is None else _segment(row)
