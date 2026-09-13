"""evaluation_runs.kind gains OPENING (Plan 2 close-out entry 10, D16).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_KINDS_BEFORE = "'LIVE', 'REPLAY', 'ACTIONABILITY', 'END_OF_DAY'"


def upgrade() -> None:
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        "ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check "
        f"CHECK (kind IN ({_KINDS_BEFORE}, 'OPENING'))"
    )


def downgrade() -> None:
    # Fails loudly if OPENING runs exist: append-only history is never rewritten to fit a downgrade.
    op.execute("ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_kind_check")
    op.execute(
        f"ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_kind_check CHECK (kind IN ({_KINDS_BEFORE}))"
    )
