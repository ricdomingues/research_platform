"""Dataset families and their dated revisions (Plan 6, D102).

`dataset_version` alone cannot be the reproducible unit: the provider can correct twenty candles tomorrow
without any configuration changing. The family holds the configuration — provider, feed, base granularity,
rule versions — and a revision holds a watermark and a manifest, so a published statistic pins a revision and
stays reproducible whatever arrives later.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, and_, func, select, text

from virtual_orders.storage.database import RESEARCH_DATASET_LOCK_KEY
from virtual_orders.storage.tables import research_dataset_revisions, research_datasets

OPERATIONAL_DATASET_NAME = "OPERATIONAL_ALPACA_IEX_1M"


def ensure_dataset(
    conn: Connection,
    *,
    name: str,
    family_version: str,
    provider: str,
    feed: str,
    base_timeframe: str,
    aggregation_version: str,
    calendar_version: str,
    adjustment_version: str | None = None,
    survivorship_bias_status: str = "PRESENT",
    data_availability_bias: str = "PRESENT",
) -> UUID:
    """The dataset family, created once. Both biases default to PRESENT: claiming their absence needs proof."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": RESEARCH_DATASET_LOCK_KEY})
    existing: UUID | None = conn.execute(
        select(research_datasets.c.dataset_id).where(and_(
            research_datasets.c.name == name,
            research_datasets.c.dataset_family_version == family_version,
        ))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    dataset_id = uuid4()
    conn.execute(research_datasets.insert().values(
        dataset_id=dataset_id, name=name, dataset_family_version=family_version, provider=provider,
        feed=feed, base_timeframe=base_timeframe, aggregation_version=aggregation_version,
        calendar_version=calendar_version, adjustment_version=adjustment_version,
        survivorship_bias_status=survivorship_bias_status,
        data_availability_bias=data_availability_bias,
    ))
    return dataset_id


def open_revision(
    conn: Connection, *, dataset_id: UUID, data_as_of: datetime, manifest_hash: str
) -> UUID:
    """Record a new revision of a dataset. Revision numbers are dense and never reused."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": RESEARCH_DATASET_LOCK_KEY})
    if data_as_of.tzinfo is None:
        raise ValueError("data_as_of must be timezone-aware")
    last: int | None = conn.execute(
        select(func.max(research_dataset_revisions.c.revision_number))
        .where(research_dataset_revisions.c.dataset_id == dataset_id)
    ).scalar_one()
    revision_id = uuid4()
    conn.execute(research_dataset_revisions.insert().values(
        revision_id=revision_id, dataset_id=dataset_id, revision_number=(last or 0) + 1,
        data_as_of=data_as_of, manifest_hash=manifest_hash,
    ))
    return revision_id


def revision_as_of(conn: Connection, *, dataset_id: UUID, as_of: datetime) -> UUID | None:
    """The newest revision whose watermark does not run past `as_of`."""
    return conn.execute(
        select(research_dataset_revisions.c.revision_id).where(and_(
            research_dataset_revisions.c.dataset_id == dataset_id,
            research_dataset_revisions.c.data_as_of <= as_of,
        )).order_by(research_dataset_revisions.c.data_as_of.desc()).limit(1)
    ).scalar_one_or_none()
