"""SQLAlchemy Core mirror of migrations/versions/0001_initial_schema.py (checked by test_schema)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = MetaData()


def _ts(name: str, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


def _num(name: str, nullable: bool = False) -> Column[Any]:
    return Column(name, Numeric(asdecimal=True), nullable=nullable)


signals = Table(
    "signals", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("client_signal_id", Text, nullable=False, unique=True),
    Column("payload_hash", Text, nullable=False),
    _ts("created_at"),
    Column("strategy", Text, nullable=False),
    Column("strategy_version", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("ticker", Text, nullable=False),
    Column("direction", Text, nullable=False),
    _num("entry_zone_low"), _num("entry_zone_high"), _num("trigger_price", True),
    Column("confirmation_note", Text),
    _num("target1"), _num("target2", True), _num("stop"),
    Column("valid_sessions", Integer, nullable=False),
    _ts("evaluation_start_ts"), _ts("valid_until_ts"),
    _num("score", True),
    Column("thesis", Text),
    Column("raw_payload", JSONB, nullable=False),
)

market_data_snapshots = Table(
    "market_data_snapshots", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    _ts("created_at"),
    Column("source", Text, nullable=False),
    _ts("data_as_of"),
    Column("tickers", ARRAY(Text), nullable=False),
    _ts("range_from"), _ts("range_to"),
    Column("content_manifest_hash", Text, nullable=False),
)

orders = Table(
    "orders", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("signal_id", UUID(as_uuid=True), nullable=False),
    Column("origin", Text, nullable=False),
    _ts("created_at"), _ts("evaluation_start_ts"), _ts("valid_until_ts"),
    Column("fill_model_version", Text, nullable=False),
    Column("config_snapshot", JSONB, nullable=False),
    Column("code_version", Text, nullable=False),
    Column("replay", Boolean, nullable=False),
    Column("replay_mode", Text),
    Column("replay_of_order_id", UUID(as_uuid=True)),
    Column("market_data_snapshot_id", UUID(as_uuid=True)),
    _num("risk_amount"),
    Column("price_source", Text, nullable=False),
)

bar_batches = Table(
    "bar_batches", metadata,
    Column("batch_id", UUID(as_uuid=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("provider_version", Text, nullable=False),
    Column("data_tier", Text, nullable=False),
    Column("request", JSONB, nullable=False),
    Column("content_hash", Text, nullable=False),
    _ts("ingested_at"),
)

bars_1m = Table(
    "bars_1m", metadata,
    Column("ticker", Text, primary_key=True),
    Column("ts", DateTime(timezone=True), primary_key=True),
    _num("open"), _num("high"), _num("low"), _num("close"), _num("volume"),
    Column("source", Text, primary_key=True),
    Column("batch_id", UUID(as_uuid=True), primary_key=True),
)

order_events = Table(
    "order_events", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("order_id", UUID(as_uuid=True), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("event_key", Text, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("hash_material", Text, nullable=False),
    Column("type", Text, nullable=False),
    _ts("bar_ts", True),
    _num("price", True), _num("qty", True),
    Column("bar_batch_id", UUID(as_uuid=True)),
    Column("payload", JSONB, nullable=False),
    _ts("recorded_at"),
)

order_state = Table(
    "order_state", metadata,
    Column("order_id", UUID(as_uuid=True), primary_key=True),
    Column("status", Text, nullable=False),
    Column("zone_lost", Boolean, nullable=False),
    _ts("entry_eligible_from", True), _ts("trigger_hit_at", True),
    Column("entry_path", Text),
    _num("avg_entry", True), _num("initial_stop", True), _num("stop_current", True),
    _ts("stop_active_from", True),
    _num("qty_total"), _num("qty_open"), _num("realized_pnl"), _num("costs"), _num("r_multiple"),
    _num("mfe_r", True), _num("mae_r", True),
    _ts("opened_at", True), _ts("closed_at", True), _ts("final_event_ts", True), _ts("last_bar_ts", True),
    Column("expected_bars", Integer, nullable=False),
    Column("missing_bars", Integer, nullable=False),
    Column("needs_review", Boolean, nullable=False),
    Column("frozen", Boolean, nullable=False),
    Column("state_document", JSONB, nullable=False),
    Column("next_seq", Integer, nullable=False),
    _ts("updated_at"),
)

evaluation_runs = Table(
    "evaluation_runs", metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("kind", Text, nullable=False),
    _ts("data_as_of"),
    Column("code_version", Text, nullable=False),
    _ts("started_at"),
)

evaluation_run_status = Table(
    "evaluation_run_status", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    _ts("recorded_at"),
    Column("detail", JSONB, nullable=False),
)

order_eval_segments = Table(
    "order_eval_segments", metadata,
    Column("order_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    _ts("bar_from"), _ts("bar_to"),
    Column("selected_data_hash", Text, nullable=False),
    Column("first_seq", Integer, nullable=False),
    Column("event_count", Integer, nullable=False),
)

dividends = Table(
    "dividends", metadata,
    Column("ticker", Text, primary_key=True),
    Column("ex_date", Date, primary_key=True),
    _num("amount"),
    Column("pay_date", Date),
    Column("sources", ARRAY(Text), nullable=False),
    Column("validated", Boolean, nullable=False),
    _ts("checked_at"),
)

integrity_incidents = Table(
    "integrity_incidents", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),
    Column("order_id", UUID(as_uuid=True)),
    Column("event_key", Text),
    Column("existing_hash", Text),
    Column("attempted_hash", Text),
    Column("detail", JSONB, nullable=False),
    _ts("recorded_at"),
)

APPEND_ONLY_TABLES = (
    "signals", "orders", "order_events", "evaluation_runs", "evaluation_run_status",
    "order_eval_segments", "bar_batches", "bars_1m", "market_data_snapshots", "integrity_incidents",
)
