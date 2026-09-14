"""One recheck row per (order, session), enforced by the schema (Plan 3B close-out T10; Plan 3C D52).

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fails loudly if duplicates already exist: append-only history is never rewritten to satisfy a constraint.
    op.execute(
        "ALTER TABLE data_quality_rechecks "
        "ADD CONSTRAINT data_quality_rechecks_order_session_key UNIQUE (order_id, session_date)"
    )
    op.execute("DROP INDEX data_quality_rechecks_session_idx")  # the unique index covers the same columns


def downgrade() -> None:
    op.execute("CREATE INDEX data_quality_rechecks_session_idx ON data_quality_rechecks (order_id, session_date)")
    op.execute("ALTER TABLE data_quality_rechecks DROP CONSTRAINT data_quality_rechecks_order_session_key")
