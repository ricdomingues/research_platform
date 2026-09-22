"""The model registry (Plan 5, D90): every trained model, kept forever, never overwritten.

`research_models` is append-only and `model_version` is unique, so registering is an insert or a no-op — never
an update. A retrained model is a new row with its own version, metrics and artifact hash; the previous one
stays exactly as it was, because a prediction stored last month has to remain explainable by the model that
actually produced it.

The artifact itself lives in the row as a JSON document rather than a binary blob somewhere else: it is small,
it is readable, and a backup of the database is therefore a complete backup of the research.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from virtual_orders.research.ml.model import GbdtModel
from virtual_orders.research.ml.training import MODEL_NAME, TrainedModel
from virtual_orders.research.repository import document
from virtual_orders.storage.tables import research_models


class ModelNotFound(LookupError):
    """No model is registered under that version."""


@dataclass(frozen=True)
class RegisteredModel:
    """A model read back out of the registry, with everything a prediction must be reported beside."""

    model_name: str
    model_version: str
    feature_version: str
    label_version: str
    layout_hash: str
    artifact_hash: str
    training_from: datetime
    training_to: datetime
    training_rows: int
    training_metrics: dict[str, Any]
    validation_metrics: dict[str, Any]
    created_at: datetime
    model: GbdtModel


def register_model(
    conn: Connection, *, run_id: UUID, trained: TrainedModel, data_as_of: datetime
) -> bool:
    """Insert a trained model. Returns False when that version is already registered; never updates a row."""
    statement = (
        pg_insert(research_models)
        .values(
            run_id=run_id, model_name=trained.model_name, model_version=trained.model_version,
            model_kind=trained.model.kind, framework=trained.model.framework,
            training_version=trained.training_version, feature_version=trained.feature_version,
            label_version=trained.label_version, layout_hash=trained.layout_hash,
            training_from=trained.training_from, training_to=trained.training_to,
            training_rows=trained.training_rows,
            hyperparameters=document(trained.model.hyperparameters.as_document()),
            training_metrics=document(trained.training_metrics.as_document()),
            validation_metrics=document(trained.validation_metrics.as_document()),
            artifact=document(trained.model.as_document()), artifact_hash=trained.artifact_hash,
            data_as_of=data_as_of,
        )
        .on_conflict_do_nothing(index_elements=["model_version"])
        .returning(research_models.c.id)
    )
    return conn.execute(statement).scalar_one_or_none() is not None


def _registered(row: Any) -> RegisteredModel:
    return RegisteredModel(
        model_name=row.model_name, model_version=row.model_version, feature_version=row.feature_version,
        label_version=row.label_version, layout_hash=row.layout_hash, artifact_hash=row.artifact_hash,
        training_from=row.training_from, training_to=row.training_to, training_rows=row.training_rows,
        training_metrics=dict(row.training_metrics), validation_metrics=dict(row.validation_metrics),
        created_at=row.created_at, model=GbdtModel.from_document(dict(row.artifact)),
    )


def load_model(conn: Connection, model_version: str) -> RegisteredModel:
    row = conn.execute(
        select(research_models).where(research_models.c.model_version == model_version)
    ).first()
    if row is None:
        raise ModelNotFound(model_version)
    return _registered(row)


def latest_model(
    conn: Connection, *, model_name: str = MODEL_NAME, feature_version: str | None = None
) -> RegisteredModel | None:
    """The newest registered model, optionally restricted to one feature version.

    Newest is by `created_at` then `id`: registration order, not training window, because two models trained on
    overlapping windows are still distinct artifacts and the later registration is the later decision.
    """
    query = select(research_models).where(research_models.c.model_name == model_name)
    if feature_version is not None:
        query = query.where(research_models.c.feature_version == feature_version)
    row = conn.execute(
        query.order_by(research_models.c.created_at.desc(), research_models.c.id.desc()).limit(1)
    ).first()
    return None if row is None else _registered(row)


def list_models(conn: Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    """Registry rows without their artifacts: the dashboard lists models, it does not download ensembles."""
    columns = (
        research_models.c.model_name, research_models.c.model_version, research_models.c.model_kind,
        research_models.c.framework, research_models.c.feature_version, research_models.c.label_version,
        research_models.c.training_from, research_models.c.training_to, research_models.c.training_rows,
        research_models.c.training_metrics, research_models.c.validation_metrics,
        research_models.c.artifact_hash, research_models.c.created_at,
    )
    rows = conn.execute(
        select(*columns).order_by(research_models.c.created_at.desc(), research_models.c.id.desc()).limit(limit)
    ).mappings()
    return [dict(row) for row in rows]


def model_exists(conn: Connection, *, artifact_hash: str, feature_version: str) -> bool:
    """Whether this exact artifact is already registered for this feature version."""
    return conn.execute(
        select(research_models.c.id).where(and_(
            research_models.c.artifact_hash == artifact_hash,
            research_models.c.feature_version == feature_version,
        ))
    ).first() is not None
