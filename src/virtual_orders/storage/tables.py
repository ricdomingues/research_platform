"""SQLAlchemy Core mirror of migrations/versions/*.py (checked by test_schema)."""

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

data_quality_rechecks = Table(
    "data_quality_rechecks", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("order_id", UUID(as_uuid=True), nullable=False),
    Column("session_date", Date, nullable=False),
    Column("recheck_key", Text, nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("source_run_id", UUID(as_uuid=True), nullable=False),
    _ts("data_as_of"),
    Column("payload", JSONB, nullable=False),
    _ts("recorded_at"),
)

watchlist = Table(
    "watchlist", metadata,
    Column("ticker", Text, primary_key=True),
    _ts("added_at"),
)

alert_rules = Table(
    "alert_rules", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("ticker", Text, nullable=False),
    Column("kind", Text, nullable=False),
    _num("level", True),
    Column("direction", Text),
    _num("cmf_threshold", True),
    Column("window_bars", Integer),
    Column("cooldown_minutes", Integer, nullable=False),
    _ts("created_at"),
)

alert_outbox = Table(
    "alert_outbox", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("alert_key", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    Column("subject", Text),
    _ts("subject_ts", True),
    Column("document", JSONB, nullable=False),
    _ts("created_at"),
)

alert_delivery_attempts = Table(
    "alert_delivery_attempts", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("alert_id", BigInteger, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("status_code", Integer),
    Column("error_type", Text),
    _ts("attempted_at"),
)

health_state_log = Table(
    "health_state_log", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("state", Text, nullable=False),
    Column("cause_codes", ARRAY(Text), nullable=False),
    _ts("observed_at"),
)

alert_event_marks = Table(
    "alert_event_marks", metadata,
    Column("order_event_id", BigInteger, primary_key=True),
    Column("alert_key", Text, nullable=False),
    _ts("recorded_at"),
)

worker_sessions = Table(
    "worker_sessions", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("event", Text, nullable=False),
    _ts("recorded_at"),
    Column("code_version", Text),
    Column("host_fingerprint", Text),
    Column("exit_code", Integer),
    Column("reason", Text),
)

research_runs = Table(
    "research_runs", metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("kind", Text, nullable=False),
    _ts("data_as_of"),
    Column("code_version", Text, nullable=False),
    Column("engine_version", Text, nullable=False),
    Column("config_hash", Text, nullable=False),
    Column("status", Text, nullable=False),
    _ts("started_at"), _ts("completed_at", True),
    Column("detail", JSONB, nullable=False),
)

pattern_detections = Table(
    "pattern_detections", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("ticker", Text, nullable=False),
    Column("timeframe", Text, nullable=False),
    Column("pattern", Text, nullable=False),
    Column("direction", Text, nullable=False),
    _ts("start_ts"), _ts("end_ts"),
    Column("candles", Integer, nullable=False),
    _num("geometry_score"), _num("context_score"), _num("overall_score"),
    Column("engine_version", Text, nullable=False),
    Column("price_source", Text, nullable=False),
    Column("evidence", JSONB, nullable=False),
    Column("evidence_hash", Text, nullable=False),
    _ts("data_as_of"), _ts("created_at"),
)

setup_candidates = Table(
    "setup_candidates", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("pattern_detection_id", BigInteger, nullable=False),
    Column("ticker", Text, nullable=False),
    Column("timeframe", Text, nullable=False),
    _ts("detected_at"),
    Column("direction", Text, nullable=False),
    Column("pattern", Text, nullable=False),
    Column("strategy", Text, nullable=False),
    Column("strategy_version", Text, nullable=False),
    Column("feature_version", Text, nullable=False),
    Column("scoring_version", Text, nullable=False),
    Column("feature_document", JSONB, nullable=False),
    Column("thesis_document", JSONB, nullable=False),
    _num("deterministic_score"), _num("ml_probability", True),
    Column("model_version", Text), Column("label_version", Text),
    _num("entry_zone_low", True), _num("entry_zone_high", True), _num("stop", True),
    _num("target1", True), _num("target2", True), _num("risk_reward", True),
    Column("levels_valid", Boolean, nullable=False),
    Column("levels_errors", ARRAY(Text), nullable=False),
    Column("client_signal_id", Text, nullable=False),
    Column("candidate_hash", Text, nullable=False),
    _ts("data_as_of"), _ts("created_at"),
)

research_backtests = Table(
    "research_backtests", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("ticker", Text),
    Column("timeframe", Text, nullable=False),
    Column("pattern", Text, nullable=False),
    Column("split", Text, nullable=False),
    Column("fold", Integer),
    _ts("period_from"), _ts("period_to"),
    Column("samples", Integer, nullable=False),
    Column("resolved", Integer, nullable=False),
    Column("wins", Integer, nullable=False),
    Column("losses", Integer, nullable=False),
    Column("timeouts", Integer, nullable=False),
    Column("ambiguous", Integer, nullable=False),
    _num("win_rate", True), _num("expectancy_r", True), _num("profit_factor", True), _num("avg_r", True),
    _num("max_drawdown_r"), _num("avg_mfe_r", True), _num("avg_mae_r", True),
    Column("backtest_version", Text, nullable=False),
    Column("label_version", Text, nullable=False),
    Column("ambiguity_policy", Text, nullable=False),
    Column("statistics", JSONB, nullable=False),
    _ts("data_as_of"), _ts("created_at"),
)

research_models = Table(
    "research_models", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("model_name", Text, nullable=False),
    Column("model_version", Text, nullable=False, unique=True),
    Column("model_kind", Text, nullable=False),
    Column("framework", Text, nullable=False),
    Column("training_version", Text, nullable=False),
    Column("feature_version", Text, nullable=False),
    Column("label_version", Text, nullable=False),
    Column("layout_hash", Text, nullable=False),
    _ts("training_from"), _ts("training_to"),
    Column("training_rows", Integer, nullable=False),
    Column("hyperparameters", JSONB, nullable=False),
    Column("training_metrics", JSONB, nullable=False),
    Column("validation_metrics", JSONB, nullable=False),
    Column("artifact", JSONB, nullable=False),
    Column("artifact_hash", Text, nullable=False),
    _ts("data_as_of"), _ts("created_at"),
)

APPEND_ONLY_TABLES = (
    "signals", "orders", "order_events", "evaluation_runs", "evaluation_run_status",
    "order_eval_segments", "bar_batches", "bars_1m", "market_data_snapshots", "integrity_incidents",
    "data_quality_rechecks", "alert_outbox", "alert_delivery_attempts", "alert_event_marks", "health_state_log",
    "worker_sessions", "pattern_detections", "setup_candidates", "research_backtests", "research_models",
)
