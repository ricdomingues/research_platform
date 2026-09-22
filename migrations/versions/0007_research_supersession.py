"""Supersession of research facts, dataset families and dated revisions (Plan 6, D96, D102).

Research facts stay append-only and are never edited. A later data revision that changes what the engine reads
produces a *new* fact and a supersession row pointing at the old one; a revision that removes the reading
altogether produces a retraction, with no replacement, because inventing an "empty detection" would be a lie
about what was observed.

`input_content_hash` is a CHANGE-DETECTION KEY, not an inventory of the bars a fact was built from. It is the
identity of the DATA that could have influenced a reading at that bucket — the bucket, its pattern's other
candles, and the prior-trend lookback behind them — so an identical re-ingestion under a new batch id is
recognised as the non-event it is, while a correction anywhere in that span is not. It is therefore wider than
the bars any single reading actually read, and the two must not be confused. It is nullable: rows written
before this migration have no such identity and must not be given a fabricated one.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

APPEND_ONLY = ("research_supersessions", "research_datasets", "research_dataset_revisions")

SCHEMA = """
ALTER TABLE pattern_detections ADD COLUMN input_content_hash text NULL;
ALTER TABLE setup_candidates ADD COLUMN input_content_hash text NULL;

CREATE TABLE research_datasets (
  dataset_id uuid PRIMARY KEY,
  name text NOT NULL,
  dataset_family_version text NOT NULL,
  provider text NOT NULL,
  feed text NOT NULL,
  base_timeframe text NOT NULL,
  universe_id uuid NULL,
  universe_version text NULL,
  aggregation_version text NOT NULL,
  calendar_version text NOT NULL,
  adjustment_version text NULL,
  survivorship_bias_status text NOT NULL CHECK (survivorship_bias_status IN ('PRESENT', 'ABSENT')),
  data_availability_bias text NOT NULL CHECK (data_availability_bias IN ('PRESENT', 'ABSENT')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (name, dataset_family_version)
);

CREATE TABLE research_dataset_revisions (
  revision_id uuid PRIMARY KEY,
  dataset_id uuid NOT NULL REFERENCES research_datasets(dataset_id),
  revision_number int NOT NULL CHECK (revision_number >= 1),
  data_as_of timestamptz NOT NULL,
  manifest_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (dataset_id, revision_number)
);
CREATE INDEX research_dataset_revisions_lookup_idx
  ON research_dataset_revisions (dataset_id, data_as_of DESC);

CREATE TABLE research_supersessions (
  id bigserial PRIMARY KEY,
  fact_type text NOT NULL CHECK (fact_type IN ('PATTERN_DETECTION', 'SETUP_CANDIDATE')),
  superseded_fact_id bigint NOT NULL,
  replacement_fact_id bigint NULL,
  reason text NOT NULL CHECK (reason IN ('SUPERSEDED_BY_REVISION', 'NO_LONGER_DETECTED')),
  source_run_id uuid NOT NULL,
  revision_id uuid NULL REFERENCES research_dataset_revisions(revision_id),
  superseded_at timestamptz NOT NULL,
  input_content_hash text NOT NULL,
  CHECK (
    (reason = 'SUPERSEDED_BY_REVISION' AND replacement_fact_id IS NOT NULL)
    OR (reason = 'NO_LONGER_DETECTED' AND replacement_fact_id IS NULL)
  ),
  UNIQUE (fact_type, superseded_fact_id, input_content_hash)
);
CREATE INDEX research_supersessions_fact_idx
  ON research_supersessions (fact_type, superseded_fact_id);
"""

DROP = """
DROP TABLE research_supersessions;
DROP TABLE research_dataset_revisions;
DROP TABLE research_datasets;
ALTER TABLE setup_candidates DROP COLUMN input_content_hash;
ALTER TABLE pattern_detections DROP COLUMN input_content_hash;
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


def downgrade() -> None:
    op.execute(DROP)
