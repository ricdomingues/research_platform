"""Initial schema: append-only history, projections and market data (spec 3.1).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APPEND_ONLY = (
    "signals", "orders", "order_events", "evaluation_runs", "evaluation_run_status",
    "order_eval_segments", "bar_batches", "bars_1m", "market_data_snapshots", "integrity_incidents",
)
MUTABLE = ("order_state", "dividends")

SCHEMA = """
CREATE TABLE signals (
  id uuid PRIMARY KEY,
  client_signal_id text NOT NULL UNIQUE,
  payload_hash text NOT NULL,
  created_at timestamptz NOT NULL,
  strategy text NOT NULL,
  strategy_version text NOT NULL,
  source text NOT NULL,
  ticker text NOT NULL,
  direction text NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
  entry_zone_low numeric NOT NULL,
  entry_zone_high numeric NOT NULL,
  trigger_price numeric NULL,
  confirmation_note text NULL,
  target1 numeric NOT NULL,
  target2 numeric NULL,
  stop numeric NOT NULL,
  valid_sessions int NOT NULL CHECK (valid_sessions BETWEEN 1 AND 20),
  evaluation_start_ts timestamptz NOT NULL,
  valid_until_ts timestamptz NOT NULL,
  score numeric NULL,
  thesis text NULL,
  raw_payload jsonb NOT NULL
);

CREATE TABLE market_data_snapshots (
  id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL,
  source text NOT NULL,
  data_as_of timestamptz NOT NULL,
  tickers text[] NOT NULL,
  range_from timestamptz NOT NULL,
  range_to timestamptz NOT NULL,
  content_manifest_hash text NOT NULL
);

CREATE TABLE orders (
  id uuid PRIMARY KEY,
  signal_id uuid NOT NULL REFERENCES signals(id),
  origin text NOT NULL CHECK (origin IN ('AUTO_STRATEGY', 'MANUAL_USER')),
  created_at timestamptz NOT NULL,
  evaluation_start_ts timestamptz NOT NULL,
  valid_until_ts timestamptz NOT NULL,
  fill_model_version text NOT NULL,
  config_snapshot jsonb NOT NULL,
  code_version text NOT NULL,
  replay boolean NOT NULL DEFAULT false,
  replay_mode text NULL CHECK (replay_mode IN ('REPRODUCE', 'RECALCULATE')),
  replay_of_order_id uuid NULL REFERENCES orders(id),
  market_data_snapshot_id uuid NULL REFERENCES market_data_snapshots(id),
  risk_amount numeric NOT NULL,
  price_source text NOT NULL,
  CHECK (
    (replay AND replay_mode IS NOT NULL AND replay_of_order_id IS NOT NULL)
    OR (NOT replay AND replay_mode IS NULL AND replay_of_order_id IS NULL
        AND market_data_snapshot_id IS NULL)
  )
);
CREATE INDEX orders_signal_idx ON orders (signal_id);

CREATE TABLE bar_batches (
  batch_id uuid PRIMARY KEY,
  provider text NOT NULL,
  provider_version text NOT NULL,
  data_tier text NOT NULL CHECK (data_tier IN ('RESEARCH', 'PRODUCTION')),
  request jsonb NOT NULL,
  content_hash text NOT NULL,
  ingested_at timestamptz NOT NULL
);

CREATE TABLE bars_1m (
  ticker text NOT NULL,
  ts timestamptz NOT NULL,
  open numeric NOT NULL,
  high numeric NOT NULL,
  low numeric NOT NULL,
  close numeric NOT NULL,
  volume numeric NOT NULL,
  source text NOT NULL,
  batch_id uuid NOT NULL REFERENCES bar_batches(batch_id),
  PRIMARY KEY (ticker, ts, source, batch_id)
);
CREATE INDEX bars_1m_lookup_idx ON bars_1m (ticker, source, ts);

CREATE TABLE order_events (
  id bigserial PRIMARY KEY,
  order_id uuid NOT NULL REFERENCES orders(id),
  seq int NOT NULL CHECK (seq >= 1),
  event_key text NOT NULL,
  payload_hash text NOT NULL,
  hash_material text NOT NULL,
  type text NOT NULL,
  bar_ts timestamptz NULL,
  price numeric NULL,
  qty numeric NULL,
  bar_batch_id uuid NULL REFERENCES bar_batches(batch_id),
  payload jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (order_id, seq),
  UNIQUE (order_id, event_key)
);

CREATE TABLE order_state (
  order_id uuid PRIMARY KEY REFERENCES orders(id),
  status text NOT NULL,
  zone_lost boolean NOT NULL,
  entry_eligible_from timestamptz NULL,
  trigger_hit_at timestamptz NULL,
  entry_path text NULL CHECK (entry_path IN ('DIRECT', 'RECLAIMED')),
  avg_entry numeric NULL,
  initial_stop numeric NULL,
  stop_current numeric NULL,
  stop_active_from timestamptz NULL,
  qty_total numeric NOT NULL,
  qty_open numeric NOT NULL,
  realized_pnl numeric NOT NULL,
  costs numeric NOT NULL,
  r_multiple numeric NOT NULL,
  mfe_r numeric NULL,
  mae_r numeric NULL,
  opened_at timestamptz NULL,
  closed_at timestamptz NULL,
  final_event_ts timestamptz NULL,
  last_bar_ts timestamptz NULL,
  expected_bars int NOT NULL,
  missing_bars int NOT NULL,
  needs_review boolean NOT NULL,
  frozen boolean NOT NULL,
  state_document jsonb NOT NULL,
  next_seq int NOT NULL CHECK (next_seq >= 1),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE evaluation_runs (
  run_id uuid PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('LIVE', 'REPLAY', 'ACTIONABILITY', 'END_OF_DAY')),
  data_as_of timestamptz NOT NULL,
  code_version text NOT NULL,
  started_at timestamptz NOT NULL
);

CREATE TABLE evaluation_run_status (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  status text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX evaluation_run_status_run_idx ON evaluation_run_status (run_id, id DESC);

CREATE TABLE order_eval_segments (
  order_id uuid NOT NULL REFERENCES orders(id),
  run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  bar_from timestamptz NOT NULL,
  bar_to timestamptz NOT NULL,
  selected_data_hash text NOT NULL,
  first_seq int NOT NULL CHECK (first_seq >= 1),
  event_count int NOT NULL CHECK (event_count >= 0),
  PRIMARY KEY (order_id, run_id),
  CHECK (bar_to >= bar_from)
);

CREATE TABLE dividends (
  ticker text NOT NULL,
  ex_date date NOT NULL,
  amount numeric NOT NULL,
  pay_date date NULL,
  sources text[] NOT NULL,
  validated boolean NOT NULL,
  checked_at timestamptz NOT NULL,
  PRIMARY KEY (ticker, ex_date)
);

CREATE TABLE integrity_incidents (
  id bigserial PRIMARY KEY,
  kind text NOT NULL,
  order_id uuid NULL REFERENCES orders(id),
  event_key text NULL,
  existing_hash text NULL,
  attempted_hash text NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'append-only table %: % rejected', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation';
END;
$$;
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vo_app') "
        "THEN CREATE ROLE vo_app NOLOGIN; END IF; END $$"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO vo_app")
    op.execute(f"GRANT SELECT, INSERT ON {', '.join(APPEND_ONLY)} TO vo_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(MUTABLE)} TO vo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vo_app")


def downgrade() -> None:
    for table in reversed(APPEND_ONLY + MUTABLE):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_history_mutation()")
