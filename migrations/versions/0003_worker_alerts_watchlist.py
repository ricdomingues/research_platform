"""Worker, alerts and watchlist (Plan 3B: D22, D24, D25, D26).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_KINDS_BEFORE = "'LIVE', 'REPLAY', 'ACTIONABILITY', 'END_OF_DAY', 'OPENING'"
APPEND_ONLY = ("data_quality_rechecks", "alert_outbox", "alert_delivery_attempts", "alert_event_marks", "health_state_log")
MUTABLE = ("watchlist", "alert_rules")

SCHEMA = """
CREATE TABLE data_quality_rechecks (
  id bigserial PRIMARY KEY,
  order_id uuid NOT NULL REFERENCES orders(id),
  session_date date NOT NULL,
  recheck_key text NOT NULL,
  run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  source_run_id uuid NOT NULL REFERENCES evaluation_runs(run_id),
  data_as_of timestamptz NOT NULL,
  payload jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (order_id, recheck_key)
);
CREATE INDEX data_quality_rechecks_session_idx ON data_quality_rechecks (order_id, session_date);

CREATE TABLE watchlist (
  ticker text PRIMARY KEY,
  added_at timestamptz NOT NULL
);

CREATE TABLE alert_rules (
  id uuid PRIMARY KEY,
  ticker text NOT NULL REFERENCES watchlist(ticker) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('PRICE_CROSS', 'PRESSURE')),
  level numeric NULL,
  direction text NULL CHECK (direction IN ('ABOVE', 'BELOW')),
  cmf_threshold numeric NULL,
  window_bars int NULL,
  cooldown_minutes int NOT NULL CHECK (cooldown_minutes BETWEEN 0 AND 1440),
  created_at timestamptz NOT NULL,
  CHECK (
    (kind = 'PRICE_CROSS' AND level IS NOT NULL AND level > 0 AND direction IS NOT NULL
      AND cmf_threshold IS NULL AND window_bars IS NULL)
    OR (kind = 'PRESSURE' AND level IS NULL AND direction IS NULL AND cmf_threshold IS NOT NULL
      AND cmf_threshold > 0 AND cmf_threshold < 1 AND window_bars IS NOT NULL AND window_bars BETWEEN 5 AND 390)
  )
);
CREATE INDEX alert_rules_ticker_idx ON alert_rules (ticker);

CREATE TABLE alert_outbox (
  id bigserial PRIMARY KEY,
  alert_key text NOT NULL UNIQUE,
  kind text NOT NULL CHECK (kind IN ('ORDER_EVENT', 'ORDER_REVIEW', 'INTEGRITY_INCIDENT', 'PRICE_CROSS', 'PRESSURE',
                                     'HEALTH', 'END_OF_DAY_SUMMARY')),
  subject text NULL,
  subject_ts timestamptz NULL,
  document jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX alert_outbox_subject_idx ON alert_outbox (kind, subject, subject_ts DESC);
CREATE INDEX alert_outbox_created_idx ON alert_outbox (created_at, id);

CREATE TABLE alert_delivery_attempts (
  id bigserial PRIMARY KEY,
  alert_id bigint NOT NULL REFERENCES alert_outbox(id),
  outcome text NOT NULL CHECK (outcome IN ('DELIVERED', 'FAILED', 'EXPIRED')),
  status_code int NULL,
  error_type text NULL,
  attempted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX alert_delivery_attempts_alert_idx ON alert_delivery_attempts (alert_id, outcome, attempted_at DESC);

CREATE TABLE alert_event_marks (
  order_event_id bigint PRIMARY KEY REFERENCES order_events(id),
  alert_key text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX alert_event_marks_recorded_idx ON alert_event_marks (recorded_at);

CREATE TABLE health_state_log (
  id bigserial PRIMARY KEY,
  state text NOT NULL CHECK (state IN ('HEALTHY', 'DEGRADED', 'UNHEALTHY')),
  cause_codes text[] NOT NULL,
  observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
"""


def upgrade() -> None:
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        "ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check "
        f"CHECK (kind IN ({_KINDS_BEFORE}, 'QUALITY_RECHECK', 'WATCHLIST'))"
    )
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
    op.execute(f"GRANT SELECT, INSERT ON {', '.join(APPEND_ONLY)} TO vo_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(MUTABLE)} TO vo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vo_app")


def downgrade() -> None:
    # Fails loudly if QUALITY_RECHECK/WATCHLIST runs exist: append-only history is never rewritten for a downgrade.
    for table in ("alert_event_marks", "alert_delivery_attempts", "alert_outbox", "alert_rules", "watchlist",
                  "data_quality_rechecks", "health_state_log"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        f"ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check CHECK (kind IN ({_KINDS_BEFORE}))"
    )
