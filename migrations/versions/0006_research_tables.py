"""Research observations: runs, pattern detections, setup candidates, backtests and models (Plan 5, D89).

Research facts are immutable and join the repository's append-only guarantees: a detection, a candidate, a
backtest result and a trained model are all statements about what was known at a `data_as_of`, and a later
vendor correction produces a *new* row rather than editing the old one. `research_runs` is the one mutable
table, like `order_state` and `dividends` before it: it is the control record of a scan (started, completed,
failed), not a research fact.

Idempotency is enforced by the schema rather than by application care. `pattern_detections` is unique on
(engine version, ticker, timeframe, pattern, end_ts, evidence hash): re-running the same scan at the same
watermark produces the identical hash and inserts nothing, while a corrected bar that genuinely changes the
reading produces a different hash and is stored beside the original. `setup_candidates` follows the same shape.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APPEND_ONLY = ("pattern_detections", "setup_candidates", "research_backtests", "research_models")
MUTABLE = ("research_runs",)
_TIMEFRAMES = "'5m', '15m', '30m', '1h', '4h', '1d'"

SCHEMA = f"""
CREATE TABLE research_runs (
  run_id uuid PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('RESEARCH_SCAN', 'BACKTEST', 'TRAINING')),
  data_as_of timestamptz NOT NULL,
  code_version text NOT NULL,
  engine_version text NOT NULL,
  config_hash text NOT NULL,
  status text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
  started_at timestamptz NOT NULL,
  completed_at timestamptz NULL,
  detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  CHECK (completed_at IS NULL OR completed_at >= started_at),
  CHECK (status = 'RUNNING' OR completed_at IS NOT NULL)
);
CREATE INDEX research_runs_kind_idx ON research_runs (kind, started_at DESC);

CREATE TABLE pattern_detections (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES research_runs(run_id),
  ticker text NOT NULL,
  timeframe text NOT NULL CHECK (timeframe IN ({_TIMEFRAMES})),
  pattern text NOT NULL,
  direction text NOT NULL CHECK (direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')),
  start_ts timestamptz NOT NULL,
  end_ts timestamptz NOT NULL,
  candles int NOT NULL CHECK (candles >= 1),
  geometry_score numeric NOT NULL CHECK (geometry_score BETWEEN 0 AND 1),
  context_score numeric NOT NULL CHECK (context_score BETWEEN 0 AND 1),
  overall_score numeric NOT NULL CHECK (overall_score BETWEEN 0 AND 1),
  engine_version text NOT NULL,
  price_source text NOT NULL,
  evidence jsonb NOT NULL,
  evidence_hash text NOT NULL,
  data_as_of timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (end_ts >= start_ts),
  UNIQUE (engine_version, ticker, timeframe, pattern, end_ts, evidence_hash)
);
CREATE INDEX pattern_detections_lookup_idx ON pattern_detections (ticker, timeframe, end_ts DESC);
CREATE INDEX pattern_detections_run_idx ON pattern_detections (run_id, id);

CREATE TABLE setup_candidates (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES research_runs(run_id),
  pattern_detection_id bigint NOT NULL REFERENCES pattern_detections(id),
  ticker text NOT NULL,
  timeframe text NOT NULL CHECK (timeframe IN ({_TIMEFRAMES})),
  detected_at timestamptz NOT NULL,
  direction text NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
  pattern text NOT NULL,
  strategy text NOT NULL,
  strategy_version text NOT NULL,
  feature_version text NOT NULL,
  scoring_version text NOT NULL,
  feature_document jsonb NOT NULL,
  thesis_document jsonb NOT NULL,
  deterministic_score numeric NOT NULL CHECK (deterministic_score BETWEEN 0 AND 1),
  ml_probability numeric NULL CHECK (ml_probability IS NULL OR ml_probability BETWEEN 0 AND 1),
  model_version text NULL,
  label_version text NULL,
  entry_zone_low numeric NULL,
  entry_zone_high numeric NULL,
  stop numeric NULL,
  target1 numeric NULL,
  target2 numeric NULL,
  risk_reward numeric NULL,
  levels_valid boolean NOT NULL,
  levels_errors text[] NOT NULL,
  client_signal_id text NOT NULL,
  candidate_hash text NOT NULL,
  data_as_of timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  -- A probability never stands alone: it always names the model and labels that produced it.
  CHECK (
    (ml_probability IS NULL AND model_version IS NULL AND label_version IS NULL)
    OR (ml_probability IS NOT NULL AND model_version IS NOT NULL AND label_version IS NOT NULL)
  ),
  UNIQUE (strategy_version, ticker, timeframe, pattern, detected_at, candidate_hash)
);
CREATE INDEX setup_candidates_lookup_idx ON setup_candidates (ticker, timeframe, detected_at DESC);
CREATE INDEX setup_candidates_run_idx ON setup_candidates (run_id, id);
CREATE INDEX setup_candidates_score_idx ON setup_candidates (detected_at DESC, deterministic_score DESC);

CREATE TABLE research_backtests (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES research_runs(run_id),
  ticker text NULL,
  timeframe text NOT NULL CHECK (timeframe IN ({_TIMEFRAMES})),
  pattern text NOT NULL,
  split text NOT NULL CHECK (split IN ('ALL', 'TRAIN', 'VALIDATION', 'TEST', 'FOLD')),
  fold int NULL,
  period_from timestamptz NOT NULL,
  period_to timestamptz NOT NULL,
  samples int NOT NULL CHECK (samples >= 0),
  resolved int NOT NULL CHECK (resolved >= 0),
  wins int NOT NULL CHECK (wins >= 0),
  losses int NOT NULL CHECK (losses >= 0),
  timeouts int NOT NULL CHECK (timeouts >= 0),
  ambiguous int NOT NULL CHECK (ambiguous >= 0),
  win_rate numeric NULL,
  expectancy_r numeric NULL,
  profit_factor numeric NULL,
  avg_r numeric NULL,
  max_drawdown_r numeric NOT NULL,
  avg_mfe_r numeric NULL,
  avg_mae_r numeric NULL,
  backtest_version text NOT NULL,
  label_version text NOT NULL,
  ambiguity_policy text NOT NULL,
  statistics jsonb NOT NULL,
  data_as_of timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (period_to >= period_from)
);
CREATE INDEX research_backtests_lookup_idx ON research_backtests (pattern, timeframe, split, created_at DESC);
CREATE INDEX research_backtests_run_idx ON research_backtests (run_id, id);

CREATE TABLE research_models (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES research_runs(run_id),
  model_name text NOT NULL,
  model_version text NOT NULL UNIQUE,
  model_kind text NOT NULL,
  framework text NOT NULL,
  training_version text NOT NULL,
  feature_version text NOT NULL,
  label_version text NOT NULL,
  layout_hash text NOT NULL,
  training_from timestamptz NOT NULL,
  training_to timestamptz NOT NULL,
  training_rows int NOT NULL CHECK (training_rows > 0),
  hyperparameters jsonb NOT NULL,
  training_metrics jsonb NOT NULL,
  validation_metrics jsonb NOT NULL,
  artifact jsonb NOT NULL,
  artifact_hash text NOT NULL,
  data_as_of timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (training_to >= training_from)
);
CREATE INDEX research_models_name_idx ON research_models (model_name, created_at DESC);
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
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(MUTABLE)} TO vo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vo_app")


def downgrade() -> None:
    # Dropping the research tables discards their append-only history, as the 0003 and 0005 downgrades do for
    # theirs. Nothing outside the research domain references them, so no other table is touched.
    for table in ("research_models", "research_backtests", "setup_candidates", "pattern_detections",
                  "research_runs"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
