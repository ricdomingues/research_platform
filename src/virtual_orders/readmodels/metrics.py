"""GET /metrics (spec 5.4): TradeResult from order_state, review policy and replay filter applied here (D19)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, select

from core.domain.models import Direction
from core.metrics.summary import MetricsSummary, TradeResult, execution_counts, summarize
from virtual_orders.storage.codec import state_from_document
from virtual_orders.storage.tables import order_state, orders, signals


class GroupBy(StrEnum):
    STRATEGY = "strategy"
    ORIGIN = "origin"
    FILL_MODEL_VERSION = "fill_model_version"
    ENTRY_PATH = "entry_path"


@dataclass(frozen=True)
class MetricsGroup:
    key: str | None
    summary: MetricsSummary


def metrics_by_group(
    conn: Connection,
    *,
    group_by: GroupBy | None,
    created_from: datetime | None,
    created_to: datetime | None,
    replay: bool,
    include_needs_review: bool,
    resamples: int,
    seed: int,
) -> list[MetricsGroup]:
    query = (
        select(
            orders.c.id, orders.c.origin, orders.c.fill_model_version, signals.c.strategy, signals.c.direction,
            order_state.c.entry_path, order_state.c.opened_at, order_state.c.closed_at, order_state.c.r_multiple,
            order_state.c.mfe_r, order_state.c.mae_r, order_state.c.state_document,
        )
        .select_from(
            orders.join(signals, signals.c.id == orders.c.signal_id)
            .join(order_state, order_state.c.order_id == orders.c.id)
        )
        .where(orders.c.replay.is_(replay))
    )
    if created_from is not None:
        query = query.where(orders.c.created_at >= created_from)
    if created_to is not None:
        query = query.where(orders.c.created_at < created_to)
    members: dict[str | None, list[Any]] = defaultdict(list)
    for row in conn.execute(query.order_by(orders.c.created_at, orders.c.id)):
        key = None if group_by is None else getattr(row, group_by.value)
        members[key].append(row)
    if group_by is None and not members:
        members[None] = []  # spec 5.4: the answer always reports excluded_needs_review, even with no orders

    groups: list[MetricsGroup] = []
    for key in sorted(members, key=lambda k: (k is None, k or "")):
        rows = members[key]
        trades = [
            TradeResult(str(r.id), Direction(r.direction), r.opened_at, r.closed_at, r.r_multiple, r.mfe_r, r.mae_r,
                        tuple(r.state_document["review_reasons"]))
            for r in rows if r.state_document["status"] == "CLOSED"
        ]
        counts = execution_counts([state_from_document(r.state_document) for r in rows])
        summary = summarize(trades, counts, include_needs_review=include_needs_review, resamples=resamples, seed=seed)
        groups.append(MetricsGroup(key, summary))
    return groups
