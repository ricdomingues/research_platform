"""Worker sessions and the daily observation alert kind (Plan 4: D62, D70).

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_KINDS_BEFORE = (
    "'ORDER_EVENT', 'ORDER_REVIEW', 'INTEGRITY_INCIDENT', 'PRICE_CROSS', 'PRESSURE', 'HEALTH', 'END_OF_DAY_SUMMARY'"
)
APPEND_ONLY = ("worker_sessions",)

SCHEMA = """
CREATE TABLE worker_sessions (
  id bigserial PRIMARY KEY,
  session_id uuid NOT NULL,
  event text NOT NULL CHECK (event IN ('STARTED', 'STOPPED')),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  code_version text NULL,
  host_fingerprint text NULL,
  exit_code int NULL,
  reason text NULL,
  UNIQUE (session_id, event),
  CHECK (
    (event = 'STARTED' AND code_version IS NOT NULL AND exit_code IS NULL AND reason IS NULL
      AND (host_fingerprint IS NULL OR host_fingerprint ~ '^[0-9a-f]{16}$'))
    OR (event = 'STOPPED' AND code_version IS NULL AND host_fingerprint IS NULL AND exit_code IS NOT NULL
      AND reason IN ('SIGNAL', 'LOCK_LOST', 'SCHEDULER_STOPPED', 'UNCAUGHT_EXCEPTION'))
  )
);
CREATE INDEX worker_sessions_recorded_idx ON worker_sessions (event, recorded_at, id);
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
    op.execute(f"GRANT SELECT, INSERT ON {', '.join(APPEND_ONLY)} TO vo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vo_app")
    op.execute("ALTER TABLE alert_outbox DROP CONSTRAINT alert_outbox_kind_check")
    op.execute(
        "ALTER TABLE alert_outbox ADD CONSTRAINT alert_outbox_kind_check "
        f"CHECK (kind IN ({_KINDS_BEFORE}, 'OBSERVATION_DAILY'))"
    )


def downgrade() -> None:
    # Fails loudly if OBSERVATION_DAILY alerts exist: append-only history is never rewritten for a downgrade.
    # Dropping worker_sessions discards its append-only history, as the 0003 downgrade does for its tables.
    op.execute("ALTER TABLE alert_outbox DROP CONSTRAINT alert_outbox_kind_check")
    op.execute(f"ALTER TABLE alert_outbox ADD CONSTRAINT alert_outbox_kind_check CHECK (kind IN ({_KINDS_BEFORE}))")
    op.execute("DROP TABLE IF EXISTS worker_sessions CASCADE")
