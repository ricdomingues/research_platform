# Plano 6, Fase 0A — Supersessão, identidade semântica e revisões de dataset

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give research facts the ability to be superseded or retracted by a later data revision, make a fact's
identity depend on what was observed rather than when, and pin every statistic to a reproducible dataset
revision — so that the Phase 0B spike's mandatory proof becomes writable.

**Architecture:** Three new append-only tables (`research_datasets`, `research_dataset_revisions`,
`research_supersessions`) plus one new nullable column on the two existing research fact tables. The scan gains
a reconciliation step: when a revisited candle's input data has genuinely changed, the detections it produces
now are compared against the facts still active for that bucket, and the difference is written as supersession
or retraction facts. Nothing is ever updated or deleted.

**Tech Stack:** Python 3.12, SQLAlchemy Core (no ORM), Alembic, PostgreSQL 16, pytest against a real Postgres,
`mypy --strict`, `ruff`. No new dependency is permitted.

**Spec:** `docs/superpowers/specs/2026-09-22-historical-evidence-design.md` — read D96, D97 and D102 before
starting. This plan implements only those three, at the minimum needed to unblock the spike.

**Branch:** `plan-6-historical-evidence`, based on `main` at `6baffeb`.

## Global Constraints

- **`src/core` is frozen.** `git diff --exit-code plan/virtual-order-engine-core-complete -- src/core` must stay
  empty. The research code imports it as a stable external library and never edits it.
- **No new external dependency.** `uv.lock`, `dashboard/uv.lock`, both `pyproject.toml` and
  `.github/workflows/ci.yml` must have no diff against `main`.
- **Money and measurements are `Decimal`.** Floats appear only at the ML and chart borders. Quantize through
  `virtual_orders.research.models.quantized` / `quantize_ratio` / `quantize_score`, never with `round()`.
- **Research fact tables are append-only**, enforced by the `reject_history_mutation()` trigger. Never write an
  `UPDATE` or `DELETE` against `pattern_detections`, `setup_candidates`, `research_backtests`,
  `research_models` or `research_supersessions`. `research_runs` is the only mutable research table.
- **Comments, docstrings, identifiers and commit messages are in English.** Documents under `docs/` are in
  Portuguese. Match the surrounding file.
- **Every rule that changes what a scan produces is versioned** and hashed into the run's configuration
  snapshot.
- **Tests run as CI runs them:** `uv run pytest -p no:cacheprovider -o addopts="" -q`, `uv run ruff check src
  tests migrations`, `uv run mypy`. Integration tests need the test Postgres at
  `postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres` (`docker compose -f docker-compose.test.yml up -d`).
- **Migrations must round-trip:** `alembic upgrade head` → `alembic downgrade base` → `alembic upgrade head`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/virtual_orders/research/identity.py` | **New.** Pure content identity of the bars behind a reading. No I/O, no platform imports. |
| `migrations/versions/0007_research_supersession.py` | **New.** The three tables, the two columns, triggers and grants. |
| `src/virtual_orders/storage/tables.py` | Add the three table definitions and the two columns. |
| `src/virtual_orders/research/datasets.py` | **New.** Dataset family and revision records (D102). |
| `src/virtual_orders/research/features.py` | `features-v2`: semantic identity separated from provenance (D97). |
| `src/virtual_orders/research/repository.py` | Persist the input hash; write supersessions; read the active view. |
| `src/virtual_orders/research/reconcile.py` | **New.** Pure decision: given found detections and active stored ones, what is superseded and what is retracted. |
| `src/virtual_orders/research/service.py` | Call reconciliation during the revisit; drop the `8b883fb` workaround. |
| `src/virtual_orders/readmodels/research.py` | Serve the active view by default. |
| `tests/research/test_identity.py` | **New.** Content hash properties. |
| `tests/research/test_reconcile.py` | **New.** The pure supersession decision. |
| `tests/integration/test_research_supersession.py` | **New.** Round trip against real Postgres, including the canary fixture. |
| `tests/fixtures/research/canary_retraction.json` | **New.** The exported canary case, as data. |

---

### Task 1: Content identity of a reading

A reading's identity must depend on the **data** behind it, not on which batch delivered it. This is the gate
that stops an identical re-ingestion from looking like a revision (D96).

**Files:**
- Create: `src/virtual_orders/research/identity.py`
- Test: `tests/research/test_identity.py`

**Interfaces:**
- Consumes: `core.domain.models.Bar`, `core.domain.hashing.sha256_hex`,
  `virtual_orders.research.models.Candle`.
- Produces:
  - `bars_content_hash(bars: Sequence[Bar]) -> str`
  - `candle_input_bars(bars: Sequence[Bar], candle: Candle) -> list[Bar]`
  - `candle_input_hash(bars: Sequence[Bar], candle: Candle) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_identity.py`:

```python
"""Content identity of the bars behind a reading (Plan 6, D96)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from core.domain.models import Bar
from virtual_orders.research.identity import (
    bars_content_hash,
    candle_input_bars,
    candle_input_hash,
)
from virtual_orders.research.models import Candle, Timeframe


def bar(minute: int, close: str = "10.00", batch_id=None) -> Bar:
    ts = datetime(2026, 9, 21, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
    return Bar(ts=ts, open=Decimal("10.00"), high=Decimal("10.50"), low=Decimal("9.50"),
               close=Decimal(close), volume=Decimal("1000"), batch_id=batch_id or uuid4())


def candle(minutes: int = 15) -> Candle:
    start = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)
    return Candle(
        timeframe=Timeframe.M15, ts=start, end_ts=start + timedelta(minutes=minutes - 1),
        session_day=start.date(), open=Decimal("10.00"), high=Decimal("10.50"), low=Decimal("9.50"),
        close=Decimal("10.00"), volume=Decimal("15000"), minutes_expected=minutes,
        minutes_present=minutes, truncated=False,
    )


def test_the_same_data_delivered_by_a_different_batch_has_the_same_identity():
    """A re-ingestion that changes nothing is a non-event: same content, same hash, no supersession."""
    first = [bar(index) for index in range(15)]
    second = [bar(index) for index in range(15)]  # fresh batch ids, identical values
    assert [item.batch_id for item in first] != [item.batch_id for item in second]
    assert bars_content_hash(first) == bars_content_hash(second)


def test_a_corrected_close_changes_the_identity():
    original = [bar(index) for index in range(15)]
    corrected = [bar(index) for index in range(14)] + [bar(14, close="10.25")]
    assert bars_content_hash(original) != bars_content_hash(corrected)


def test_a_missing_bar_changes_the_identity():
    """Absence is data: a candle built from 14 minutes is not the candle built from 15."""
    full = [bar(index) for index in range(15)]
    assert bars_content_hash(full) != bars_content_hash(full[:-1])


def test_only_the_bars_inside_the_candle_count():
    series = [bar(index) for index in range(30)]
    inside = candle_input_bars(series, candle())
    assert [item.ts for item in inside] == [item.ts for item in series[:15]]
    assert candle_input_hash(series, candle()) == bars_content_hash(series[:15])


def test_order_is_normalised_so_read_order_cannot_change_identity():
    series = [bar(index) for index in range(15)]
    assert bars_content_hash(list(reversed(series))) == bars_content_hash(series)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/research/test_identity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'virtual_orders.research.identity'`

- [ ] **Step 3: Write the implementation**

Create `src/virtual_orders/research/identity.py`:

```python
"""Content identity of the bars behind a reading (Plan 6, D96).

A reading's identity is the DATA it was built from, never the batch that delivered it. Re-ingesting the same
minute under a new `batch_id` is a non-event and must not look like a vendor revision: only a change in the
bars' own values, or in which minutes are present at all, is a reason to re-derive anything.

Pure: no I/O, no platform imports beyond the shared `Bar` and hashing helpers.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.domain.hashing import sha256_hex
from core.domain.models import Bar
from virtual_orders.research.models import Candle


def bars_content_hash(bars: Sequence[Bar]) -> str:
    """Identity of a set of bars: their timestamps and values, sorted, with provenance deliberately excluded.

    `batch_id` is absent on purpose. Absence of a minute is itself part of the identity, so a candle built
    from fourteen minutes never hashes like the same candle built from fifteen.
    """
    return sha256_hex([
        {"ts": item.ts, "open": item.open, "high": item.high, "low": item.low,
         "close": item.close, "volume": item.volume}
        for item in sorted(bars, key=lambda item: item.ts)
    ])


def candle_input_bars(bars: Sequence[Bar], candle: Candle) -> list[Bar]:
    """The bars that fell inside `candle`'s bucket, in chronological order."""
    return sorted(
        (item for item in bars if candle.ts <= item.ts <= candle.end_ts),
        key=lambda item: item.ts,
    )


def candle_input_hash(bars: Sequence[Bar], candle: Candle) -> str:
    """Identity of the data behind one candle."""
    return bars_content_hash(candle_input_bars(bars, candle))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/research/test_identity.py -q`
Expected: 5 passed

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src tests migrations && uv run mypy`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/research/identity.py tests/research/test_identity.py
git commit -m "feat(research): content identity of the bars behind a reading

A reading's identity is the data it was built from, not the batch that delivered it. Re-ingesting the same
minute under a new batch_id must not look like a vendor revision, so batch_id is excluded from the hash and
only the values, and which minutes are present at all, decide identity."
```

---

### Task 2: Schema for supersession, datasets and revisions

**Files:**
- Create: `migrations/versions/0007_research_supersession.py`
- Modify: `src/virtual_orders/storage/tables.py` (add three tables; add `input_content_hash` to
  `pattern_detections` and `setup_candidates`)
- Test: `tests/integration/test_research_supersession.py`

**Interfaces:**
- Produces: tables `research_datasets`, `research_dataset_revisions`, `research_supersessions`; columns
  `pattern_detections.input_content_hash` and `setup_candidates.input_content_hash` (both nullable `text`).

The new column is **nullable** on purpose: the 1,204 rows already stored were written before the input hash
existed, and inventing a value for them would be a lie. `NULL` means "input identity unknown", and Task 6
treats such a row as always needing reconciliation rather than assuming it is current.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_research_supersession.py`:

```python
"""Supersession schema and its append-only guarantees (Plan 6, D96, D102)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from virtual_orders.storage import tables


def test_the_new_tables_exist_with_their_append_only_triggers(engine):
    with engine.connect() as conn:
        present = set(conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )).scalars())
    assert {"research_datasets", "research_dataset_revisions", "research_supersessions"} <= present


def test_a_supersession_can_never_be_updated_or_deleted(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO research_supersessions (fact_type, superseded_fact_id, replacement_fact_id, reason,"
            " source_run_id, superseded_at, input_content_hash)"
            " VALUES ('PATTERN_DETECTION', 1, NULL, 'NO_LONGER_DETECTED', gen_random_uuid(), now(), 'abc')"
        ))
    with pytest.raises(Exception, match="append-only|immutable|history"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE research_supersessions SET reason = 'SUPERSEDED_BY_REVISION'"))
    with pytest.raises(Exception, match="append-only|immutable|history"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM research_supersessions"))


def test_a_reason_outside_the_vocabulary_is_rejected(engine):
    with pytest.raises(Exception, match="check constraint|violates"):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO research_supersessions (fact_type, superseded_fact_id, replacement_fact_id,"
                " reason, source_run_id, superseded_at, input_content_hash)"
                " VALUES ('PATTERN_DETECTION', 1, NULL, 'BECAUSE_I_SAID_SO', gen_random_uuid(), now(), 'abc')"
            ))


def test_a_retraction_has_no_replacement_and_a_supersession_must_have_one(engine):
    """The two cases of D96 are distinguishable by the row itself, not by convention."""
    with pytest.raises(Exception, match="check constraint|violates"):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO research_supersessions (fact_type, superseded_fact_id, replacement_fact_id,"
                " reason, source_run_id, superseded_at, input_content_hash)"
                " VALUES ('PATTERN_DETECTION', 1, NULL, 'SUPERSEDED_BY_REVISION', gen_random_uuid(), now(), 'a')"
            ))


def test_the_fact_tables_carry_an_input_content_hash(engine):
    assert "input_content_hash" in tables.pattern_detections.c
    assert "input_content_hash" in tables.setup_candidates.c
    with engine.connect() as conn:
        columns = set(conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'pattern_detections'"
        )).scalars())
    assert "input_content_hash" in columns
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_research_supersession.py -q`
Expected: FAIL — the tables do not exist

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0007_research_supersession.py`:

```python
"""Supersession of research facts, dataset families and dated revisions (Plan 6, D96, D102).

Research facts stay append-only and are never edited. A later data revision that changes what the engine reads
produces a *new* fact and a supersession row pointing at the old one; a revision that removes the reading
altogether produces a retraction, with no replacement, because inventing an "empty detection" would be a lie
about what was observed.

`input_content_hash` records the identity of the DATA a fact was built from, so an identical re-ingestion under
a new batch id is recognised as the non-event it is. It is nullable: rows written before this migration have no
such identity and must not be given a fabricated one.

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
    op.execute("GRANT USAGE, SELECT ON SEQUENCE research_supersessions_id_seq TO vo_app")


def downgrade() -> None:
    op.execute(DROP)
```

> Check `migrations/versions/0006_research_tables.py` for how it grants sequence usage and confirm the exact
> role name (`vo_app`) and the grant style used there; copy that style rather than the lines above if they
> differ.

- [ ] **Step 4: Add the table definitions**

In `src/virtual_orders/storage/tables.py`, add `Column("input_content_hash", Text, nullable=True)` to both
`pattern_detections` and `setup_candidates` (immediately before `_ts("data_as_of")`), and append:

```python
research_datasets = Table(
    "research_datasets", metadata,
    Column("dataset_id", UUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("dataset_family_version", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("feed", Text, nullable=False),
    Column("base_timeframe", Text, nullable=False),
    Column("universe_id", UUID(as_uuid=True), nullable=True),
    Column("universe_version", Text, nullable=True),
    Column("aggregation_version", Text, nullable=False),
    Column("calendar_version", Text, nullable=False),
    Column("adjustment_version", Text, nullable=True),
    Column("survivorship_bias_status", Text, nullable=False),
    Column("data_availability_bias", Text, nullable=False),
    _ts("created_at"),
)

research_dataset_revisions = Table(
    "research_dataset_revisions", metadata,
    Column("revision_id", UUID(as_uuid=True), primary_key=True),
    Column("dataset_id", UUID(as_uuid=True), nullable=False),
    Column("revision_number", Integer, nullable=False),
    _ts("data_as_of"),
    Column("manifest_hash", Text, nullable=False),
    _ts("created_at"),
)

research_supersessions = Table(
    "research_supersessions", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("fact_type", Text, nullable=False),
    Column("superseded_fact_id", BigInteger, nullable=False),
    Column("replacement_fact_id", BigInteger, nullable=True),
    Column("reason", Text, nullable=False),
    Column("source_run_id", UUID(as_uuid=True), nullable=False),
    Column("revision_id", UUID(as_uuid=True), nullable=True),
    _ts("superseded_at"),
    Column("input_content_hash", Text, nullable=False),
)
```

Add the three table names to the append-only list near the bottom of the file (the tuple that currently ends
with `"research_backtests", "research_models"`).

- [ ] **Step 5: Run the migration round trip**

```bash
uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head
```
Expected: three clean runs, no error.

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_research_supersession.py -q`
Expected: 5 passed

- [ ] **Step 7: Run the full suite, lint and types**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q && uv run ruff check src tests migrations && uv run mypy`
Expected: all green. If a test asserting the exact set of tables or the migration count fails, update it — do
not weaken it.

- [ ] **Step 8: Commit**

```bash
git add migrations/versions/0007_research_supersession.py src/virtual_orders/storage/tables.py \
        tests/integration/test_research_supersession.py
git commit -m "feat(research): schema for supersession, dataset families and dated revisions

A later data revision produces a new fact plus a supersession row, or a retraction with no replacement when
the corrected data yields no reading at all; a check constraint makes the two cases distinguishable by the row
rather than by convention. input_content_hash is nullable because the rows written before it exist, and giving
them a fabricated identity would be worse than admitting it is unknown."
```

---

### Task 3: Dataset families and revisions

**Files:**
- Create: `src/virtual_orders/research/datasets.py`
- Test: `tests/integration/test_research_supersession.py` (append)

**Interfaces:**
- Consumes: `tables.research_datasets`, `tables.research_dataset_revisions` from Task 2.
- Produces:
  - `OPERATIONAL_DATASET_NAME: str` (`"OPERATIONAL_ALPACA_IEX_1M"`)
  - `ensure_dataset(conn, *, name, family_version, provider, feed, base_timeframe, aggregation_version,
    calendar_version, survivorship_bias_status="PRESENT", data_availability_bias="PRESENT") -> UUID`
  - `open_revision(conn, *, dataset_id: UUID, data_as_of: datetime, manifest_hash: str) -> UUID`
  - `revision_as_of(conn, *, dataset_id: UUID, as_of: datetime) -> UUID | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_research_supersession.py`:

```python
from datetime import UTC, datetime

from virtual_orders.research.datasets import (
    ensure_dataset,
    open_revision,
    revision_as_of,
)

DATASET = dict(
    name="OPERATIONAL_ALPACA_IEX_1M", family_version="v1", provider="alpaca", feed="iex",
    base_timeframe="1m", aggregation_version="timeframes-v1", calendar_version="nyse-v1",
)


def test_ensuring_a_dataset_twice_returns_the_same_identity(engine):
    with engine.begin() as conn:
        first = ensure_dataset(conn, **DATASET)
    with engine.begin() as conn:
        second = ensure_dataset(conn, **DATASET)
    assert first == second


def test_revisions_are_numbered_in_order_and_never_reused(engine):
    with engine.begin() as conn:
        dataset_id = ensure_dataset(conn, **DATASET)
        first = open_revision(conn, dataset_id=dataset_id,
                              data_as_of=datetime(2026, 9, 21, 20, 0, tzinfo=UTC), manifest_hash="aaa")
        second = open_revision(conn, dataset_id=dataset_id,
                               data_as_of=datetime(2026, 9, 22, 20, 0, tzinfo=UTC), manifest_hash="bbb")
    assert first != second
    with engine.connect() as conn:
        numbers = list(conn.execute(text(
            "SELECT revision_number FROM research_dataset_revisions ORDER BY revision_number"
        )).scalars())
    assert numbers == [1, 2]


def test_a_statistic_pinned_to_a_revision_still_resolves_after_a_later_one_exists(engine):
    """D102: a published number stays reproducible after the vendor corrects candles."""
    with engine.begin() as conn:
        dataset_id = ensure_dataset(conn, **DATASET)
        early = open_revision(conn, dataset_id=dataset_id,
                              data_as_of=datetime(2026, 9, 21, 20, 0, tzinfo=UTC), manifest_hash="aaa")
        open_revision(conn, dataset_id=dataset_id,
                      data_as_of=datetime(2026, 9, 22, 20, 0, tzinfo=UTC), manifest_hash="bbb")
    with engine.connect() as conn:
        resolved = revision_as_of(conn, dataset_id=dataset_id,
                                  as_of=datetime(2026, 9, 21, 23, 0, tzinfo=UTC))
    assert resolved == early
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_research_supersession.py -q`
Expected: FAIL — `No module named 'virtual_orders.research.datasets'`

- [ ] **Step 3: Implement**

Create `src/virtual_orders/research/datasets.py`:

```python
"""Dataset families and their dated revisions (Plan 6, D102).

`dataset_version` alone cannot be the reproducible unit: the provider can correct twenty candles tomorrow
without any configuration changing. The family holds the configuration — provider, feed, base granularity,
rule versions — and a revision holds a watermark and a manifest, so a published statistic pins a revision and
stays reproducible whatever arrives later.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, and_, func, select

from virtual_orders.storage.tables import research_dataset_revisions, research_datasets

OPERATIONAL_DATASET_NAME = "OPERATIONAL_ALPACA_IEX_1M"


def ensure_dataset(
    conn: Connection,
    *,
    name: str,
    family_version: str,
    provider: str,
    feed: str,
    base_timeframe: str,
    aggregation_version: str,
    calendar_version: str,
    adjustment_version: str | None = None,
    survivorship_bias_status: str = "PRESENT",
    data_availability_bias: str = "PRESENT",
) -> UUID:
    """The dataset family, created once. Both biases default to PRESENT: claiming their absence needs proof."""
    existing: UUID | None = conn.execute(
        select(research_datasets.c.dataset_id).where(and_(
            research_datasets.c.name == name,
            research_datasets.c.dataset_family_version == family_version,
        ))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    dataset_id = uuid4()
    conn.execute(research_datasets.insert().values(
        dataset_id=dataset_id, name=name, dataset_family_version=family_version, provider=provider,
        feed=feed, base_timeframe=base_timeframe, aggregation_version=aggregation_version,
        calendar_version=calendar_version, adjustment_version=adjustment_version,
        survivorship_bias_status=survivorship_bias_status,
        data_availability_bias=data_availability_bias,
    ))
    return dataset_id


def open_revision(
    conn: Connection, *, dataset_id: UUID, data_as_of: datetime, manifest_hash: str
) -> UUID:
    """Record a new revision of a dataset. Revision numbers are dense and never reused."""
    if data_as_of.tzinfo is None:
        raise ValueError("data_as_of must be timezone-aware")
    last: int | None = conn.execute(
        select(func.max(research_dataset_revisions.c.revision_number))
        .where(research_dataset_revisions.c.dataset_id == dataset_id)
    ).scalar_one()
    revision_id = uuid4()
    conn.execute(research_dataset_revisions.insert().values(
        revision_id=revision_id, dataset_id=dataset_id, revision_number=(last or 0) + 1,
        data_as_of=data_as_of, manifest_hash=manifest_hash,
    ))
    return revision_id


def revision_as_of(conn: Connection, *, dataset_id: UUID, as_of: datetime) -> UUID | None:
    """The newest revision whose watermark does not run past `as_of`."""
    return conn.execute(
        select(research_dataset_revisions.c.revision_id).where(and_(
            research_dataset_revisions.c.dataset_id == dataset_id,
            research_dataset_revisions.c.data_as_of <= as_of,
        )).order_by(research_dataset_revisions.c.data_as_of.desc()).limit(1)
    ).scalar_one_or_none()
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `uv run pytest tests/integration/test_research_supersession.py -q`
Expected: 8 passed

- [ ] **Step 5: Lint and types**

Run: `uv run ruff check src tests migrations && uv run mypy`

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/research/datasets.py tests/integration/test_research_supersession.py
git commit -m "feat(research): dataset families and dated revisions

A dataset version cannot be both configuration and state: the provider can correct candles without any rule
changing. The family carries the configuration and a revision carries a watermark and a manifest, so a
published statistic pins a revision and stays reproducible whatever arrives afterwards. Both biases default to
PRESENT because claiming their absence needs evidence."
```

---

### Task 4: Semantic identity — `features-v2`

**Files:**
- Modify: `src/virtual_orders/research/features.py`
- Modify: `src/virtual_orders/research/service.py` (remove the `8b883fb` workaround)
- Test: `tests/research/test_setups.py` (append), `tests/integration/test_research_scan.py` (adjust)

**Interfaces:**
- Consumes: nothing new.
- Produces: `FEATURE_VERSION == "features-v2"`; `FeatureSnapshot.feature_hash` no longer depends on
  `data_as_of`; `FeatureSnapshot.PROVENANCE_FIELDS: tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/research/test_setups.py`:

```python
def test_the_same_observation_at_two_clocks_has_one_identity():
    """D97: identity is what was observed, not when. Provenance is recorded, not hashed."""
    morning = snapshot_fixture(data_as_of=datetime(2026, 9, 21, 14, 0, tzinfo=UTC))
    evening = snapshot_fixture(data_as_of=datetime(2026, 9, 21, 20, 0, tzinfo=UTC))
    assert morning.data_as_of != evening.data_as_of
    assert morning.feature_hash == evening.feature_hash


def test_a_changed_measurement_still_changes_the_identity():
    baseline = snapshot_fixture()
    moved = snapshot_fixture(rsi14=Decimal("41.0"))
    assert baseline.feature_hash != moved.feature_hash
```

`snapshot_fixture` is built with `dataclasses.replace` over a snapshot the existing helpers already produce,
so it never has to restate every field of `FeatureSnapshot`:

```python
from dataclasses import replace


def snapshot_fixture(**overrides: object):
    """An existing snapshot with named fields swapped. Avoids restating every field of FeatureSnapshot."""
    base = build_snapshot_for_tests()   # the helper this module already uses to make a FeatureSnapshot
    return replace(base, **overrides)
```

Read the top of `tests/research/test_setups.py` and point `build_snapshot_for_tests` at whatever that module
already calls to produce a `FeatureSnapshot`; if it builds one inline, extract that into a helper first and
leave the existing tests using it.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_setups.py -q -k identity`
Expected: FAIL — the two hashes differ

- [ ] **Step 3: Implement**

In `src/virtual_orders/research/features.py`, change `FEATURE_VERSION` to `"features-v2"` and replace the
hashing block:

```python
    # What was observed, versus when we came to know it. `data_as_of` and the scan's own clock are provenance:
    # two scans over identical candles describe the same observation, and giving them different identities made
    # "how many occurrences exist" depend on how many times the scan ran.
    PROVENANCE_FIELDS = ("data_as_of",)

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def semantic_document(self) -> dict[str, Any]:
        """Everything the snapshot measured, with provenance removed."""
        return {name: value for name, value in self.as_document().items()
                if name not in self.PROVENANCE_FIELDS}

    @property
    def feature_hash(self) -> str:
        """Identity of the exact inputs a prediction was made from, independent of when they were read."""
        return sha256_hex(self.semantic_document())
```

In `src/virtual_orders/research/service.py`, delete the four-line guard added by `8b883fb`:

```python
                if not inserted:
                    # An unchanged reading of a revisited candle. ...
                    continue
```

and restore the candidate derivation for every detection. It is no longer needed: an unchanged candle now
produces an identical `candidate_hash`, so the existing `ON CONFLICT DO NOTHING` absorbs it.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/research tests/integration/test_research_scan.py -q`
Expected: pass. `test_running_the_same_scan_again_creates_no_duplicate_observations` must still report
`(0, 0)` — now because the hashes match, not because the derivation was skipped. If it fails with a non-zero
candidate count, the hash still depends on provenance: check for other clock-derived fields in the snapshot.

- [ ] **Step 5: Run the full suite, lint and types**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q && uv run ruff check src tests migrations && uv run mypy`

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/research/features.py src/virtual_orders/research/service.py \
        tests/research/test_setups.py
git commit -m "feat(research): features-v2 separates semantic identity from provenance

data_as_of inside feature_hash made two scans over identical candles describe two different observations, so
'how many occurrences exist' depended on how many times the scan ran. Identity now covers what was measured
and provenance is recorded beside it. The guard added in 8b883fb is removed: an unchanged candle hashes
identically and the existing conflict clause absorbs it, which is the fix rather than the symptom.

features-v1 observations are never pooled with features-v2 in one statistical population."
```

---

### Task 5: Persist the input hash and read the active view

**Files:**
- Modify: `src/virtual_orders/research/repository.py`
- Test: `tests/integration/test_research_supersession.py` (append)

**Interfaces:**
- Consumes: `identity.candle_input_hash` (Task 1), tables from Task 2.
- Produces:
  - `record_detection(..., input_content_hash: str | None = None)` — new keyword argument
  - `record_candidate(..., input_content_hash: str | None = None)` — new keyword argument
  - `record_supersession(conn, *, fact_type: str, superseded_fact_id: int, replacement_fact_id: int | None,
    reason: str, source_run_id: UUID, revision_id: UUID | None, superseded_at: datetime,
    input_content_hash: str) -> bool`
  - `active_detections(conn, *, ticker: str, timeframe: Timeframe, end_ts: datetime, engine_version: str,
    as_of: datetime | None = None) -> list[dict[str, Any]]`
  - `SUPERSEDED_BY_REVISION = "SUPERSEDED_BY_REVISION"`, `NO_LONGER_DETECTED = "NO_LONGER_DETECTED"`,
    `FACT_PATTERN_DETECTION = "PATTERN_DETECTION"`, `FACT_SETUP_CANDIDATE = "SETUP_CANDIDATE"`

`active_detections` excludes any detection that has a supersession row. With `as_of` given it excludes only
supersessions recorded at or before that instant, which is what makes "what was active before the correction?"
answerable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_research_supersession.py`:

```python
def test_a_retracted_detection_leaves_the_active_view_but_stays_in_the_table(engine):
    detection_id = seed_one_detection(engine)          # helper below
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(),
            revision_id=None, superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC),
            input_content_hash="corrected",
        )
    with engine.connect() as conn:
        active = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15,
                                   end_ts=DETECTION_END_TS, engine_version="candles-v1")
    assert active == []
    assert count(engine, "pattern_detections") == 1   # the fact itself is untouched


def test_the_active_view_answers_as_of_an_earlier_instant(engine):
    """Reconstructing what the platform claimed before a correction is the point of D96."""
    detection_id = seed_one_detection(engine)
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(),
            revision_id=None, superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC),
            input_content_hash="corrected",
        )
    with engine.connect() as conn:
        before = active_detections(conn, ticker="AAPL", timeframe=Timeframe.M15, end_ts=DETECTION_END_TS,
                                   engine_version="candles-v1",
                                   as_of=datetime(2026, 9, 22, 18, 0, tzinfo=UTC))
    assert [row["id"] for row in before] == [detection_id]


def test_recording_the_same_supersession_twice_is_idempotent(engine):
    detection_id = seed_one_detection(engine)
    arguments = dict(
        fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id, replacement_fact_id=None,
        reason=NO_LONGER_DETECTED, source_run_id=uuid4(), revision_id=None,
        superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC), input_content_hash="corrected",
    )
    with engine.begin() as conn:
        assert record_supersession(conn, **arguments) is True
    with engine.begin() as conn:
        assert record_supersession(conn, **arguments) is False
    assert count(engine, "research_supersessions") == 1
```

Write `seed_one_detection(engine) -> int` in the same file: insert one row into `pattern_detections` with
`ticker="AAPL"`, `timeframe="15m"`, `pattern="HAMMER"`, `engine_version="candles-v1"`,
`end_ts=DETECTION_END_TS` (a module-level aware datetime), scores `Decimal("0.5")`, `evidence={}`,
`evidence_hash="seed"`, `data_as_of` and `created_at` set, and return its `id`. Use
`tables.pattern_detections.insert().returning(...)`, following `tests/integration/support.py`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_research_supersession.py -q`
Expected: FAIL — `record_supersession` / `active_detections` do not exist

- [ ] **Step 3: Implement**

In `src/virtual_orders/research/repository.py`, add the vocabulary constants near the top, add
`input_content_hash` to the `values` dict of both `record_detection` and `record_candidate` (taking it as a
keyword argument defaulting to `None`), and append:

```python
def record_supersession(
    conn: Connection,
    *,
    fact_type: str,
    superseded_fact_id: int,
    replacement_fact_id: int | None,
    reason: str,
    source_run_id: UUID,
    revision_id: UUID | None,
    superseded_at: datetime,
    input_content_hash: str,
) -> bool:
    """Record that a later revision superseded or retracted a fact. Returns False when already recorded.

    Idempotent on (fact_type, superseded_fact_id, input_content_hash): re-running a scan at the same revision
    must not stack supersession rows for the same correction.
    """
    statement = (
        pg_insert(research_supersessions)
        .values(
            fact_type=fact_type, superseded_fact_id=superseded_fact_id,
            replacement_fact_id=replacement_fact_id, reason=reason, source_run_id=source_run_id,
            revision_id=revision_id, superseded_at=superseded_at, input_content_hash=input_content_hash,
        )
        .on_conflict_do_nothing(index_elements=["fact_type", "superseded_fact_id", "input_content_hash"])
        .returning(research_supersessions.c.id)
    )
    return conn.execute(statement).scalar_one_or_none() is not None


def active_detections(
    conn: Connection,
    *,
    ticker: str,
    timeframe: Timeframe,
    end_ts: datetime,
    engine_version: str,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Detections for one bucket that no revision has superseded or retracted.

    With `as_of`, only supersessions recorded at or before that instant count, so the view answers what was
    active then rather than only what is active now.
    """
    gone = select(research_supersessions.c.superseded_fact_id).where(
        research_supersessions.c.fact_type == FACT_PATTERN_DETECTION
    )
    if as_of is not None:
        gone = gone.where(research_supersessions.c.superseded_at <= as_of)
    rows = conn.execute(
        select(
            pattern_detections.c.id, pattern_detections.c.pattern, pattern_detections.c.evidence_hash,
            pattern_detections.c.input_content_hash,
        ).where(and_(
            pattern_detections.c.ticker == ticker,
            pattern_detections.c.timeframe == timeframe.value,
            pattern_detections.c.end_ts == end_ts,
            pattern_detections.c.engine_version == engine_version,
            pattern_detections.c.id.not_in(gone),
        )).order_by(pattern_detections.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]
```

Import `research_supersessions` from `virtual_orders.storage.tables` at the top of the module.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/integration/test_research_supersession.py -q`
Expected: 11 passed

- [ ] **Step 5: Lint, types, full suite**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q && uv run ruff check src tests migrations && uv run mypy`

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/research/repository.py tests/integration/test_research_supersession.py
git commit -m "feat(research): persist input identity and serve the active view as-of

A fact that a revision superseded or retracted leaves the active view without leaving the table, and the view
answers as-of an earlier instant, so what the platform claimed before a correction stays reconstructable.
Supersession is idempotent on the corrected input's identity: re-running a scan at the same revision records
the correction once, not once per run."
```

---

### Task 6: Reconcile a revisited bucket

The pure decision first, then wiring it into the scan.

**Files:**
- Create: `src/virtual_orders/research/reconcile.py`
- Modify: `src/virtual_orders/research/service.py`
- Test: `tests/research/test_reconcile.py`, `tests/integration/test_research_supersession.py` (append)

**Interfaces:**
- Consumes: `PatternDetection` (`research.models`), the rows returned by `active_detections` (Task 5).
- Produces:
  - `@dataclass(frozen=True) class Reconciliation: superseded: tuple[tuple[int, str], ...];
    retracted: tuple[int, ...]` — `superseded` pairs a stored fact id with the `evidence_hash` of the reading
    that replaces it.
  - `reconcile(found: Sequence[PatternDetection], active: Sequence[Mapping[str, Any]]) -> Reconciliation`

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_reconcile.py`:

```python
"""What a revisited bucket does to the facts already stored for it (Plan 6, D96)."""

from __future__ import annotations

from virtual_orders.research.reconcile import reconcile
from tests.research.support import detection  # existing helper; read it before use


def stored(fact_id: int, pattern: str, evidence_hash: str) -> dict[str, object]:
    return {"id": fact_id, "pattern": pattern, "evidence_hash": evidence_hash,
            "input_content_hash": "old"}


def test_an_identical_reading_changes_nothing():
    found = [detection(pattern="HAMMER")]
    active = [stored(1, "HAMMER", found[0].evidence_hash)]
    result = reconcile(found, active)
    assert result.superseded == () and result.retracted == ()


def test_a_changed_reading_of_the_same_pattern_supersedes_the_old_one():
    """Case A of D96: the corrected candle still says HAMMER, but not the same HAMMER."""
    found = [detection(pattern="HAMMER", geometry_score="0.90")]
    active = [stored(1, "HAMMER", "a-different-hash")]
    result = reconcile(found, active)
    assert result.superseded == ((1, found[0].evidence_hash),)
    assert result.retracted == ()


def test_a_pattern_that_no_longer_appears_is_retracted():
    """Case B of D96, and the one the Plan 5 canary could not record."""
    found = [detection(pattern="DOJI")]
    active = [stored(1, "HAMMER", "whatever")]
    result = reconcile(found, active)
    assert result.retracted == (1,)
    assert result.superseded == ()


def test_a_bucket_that_now_detects_nothing_retracts_everything_it_had():
    active = [stored(1, "HAMMER", "x"), stored(2, "DOJI", "y")]
    result = reconcile([], active)
    assert result.retracted == (1, 2)


def test_a_new_pattern_with_no_stored_counterpart_is_not_a_supersession():
    """A reading that simply did not exist before is an insert, not a correction of something else."""
    found = [detection(pattern="HAMMER"), detection(pattern="DOJI")]
    active = [stored(1, "HAMMER", found[0].evidence_hash)]
    result = reconcile(found, active)
    assert result.superseded == () and result.retracted == ()
```

If `tests/research/support.py` has no `detection` helper, add one there: build a `PatternDetection` with fixed
timestamps and scores, accepting `pattern` and `geometry_score` overrides.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_reconcile.py -q`
Expected: FAIL — `No module named 'virtual_orders.research.reconcile'`

- [ ] **Step 3: Implement the pure decision**

Create `src/virtual_orders/research/reconcile.py`:

```python
"""What a revisited bucket does to the facts already stored for it (Plan 6, D96).

Pure: given what the engine detects now and what is still active for that bucket, decide which stored facts a
revision superseded and which it retracted. Matching is by pattern, because that is what a reading claims: the
same pattern with a different `evidence_hash` is a corrected reading of the same claim, while a pattern the
corrected candle no longer produces is a claim that is simply gone.

A reading with no stored counterpart is an insert, not a correction: it is recorded by the normal path and
appears here only by its absence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from virtual_orders.research.models import PatternDetection


@dataclass(frozen=True)
class Reconciliation:
    """`superseded` pairs a stored fact id with the evidence hash of the reading that replaces it."""

    superseded: tuple[tuple[int, str], ...]
    retracted: tuple[int, ...]


def reconcile(
    found: Sequence[PatternDetection], active: Sequence[Mapping[str, Any]]
) -> Reconciliation:
    by_pattern: dict[str, PatternDetection] = {item.pattern: item for item in found}
    hashes = {item.evidence_hash for item in found}
    superseded: list[tuple[int, str]] = []
    retracted: list[int] = []
    for row in active:
        if row["evidence_hash"] in hashes:
            continue  # the same reading survived the revision untouched
        replacement = by_pattern.get(str(row["pattern"]))
        if replacement is None:
            retracted.append(int(row["id"]))
        else:
            superseded.append((int(row["id"]), replacement.evidence_hash))
    return Reconciliation(tuple(superseded), tuple(retracted))
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `uv run pytest tests/research/test_reconcile.py -q`
Expected: 5 passed

- [ ] **Step 5: Wire it into the scan**

In `src/virtual_orders/research/service.py`, inside `_scan_one`'s candle loop, replace the revisit guard so the
input hash decides whether reconciliation is needed, and reconcile after detecting:

```python
            already_read = resume is not None and candle.end_ts <= resume
            if already_read and (revisit_from is None or candle.end_ts < revisit_from):
                continue  # already scanned by this engine version, and too old to have been corrected since
            input_hash = candle_input_hash(bars, candle)
            ...
            found = detect_at(...)
            if config.patterns is not None:
                found = [item for item in found if item.pattern in config.patterns]
            if already_read:
                # A revisited bucket. Only a genuine change in the DATA is a reason to reconcile: an identical
                # re-ingestion under a new batch id is a non-event (D96).
                stored_rows = active_detections(
                    conn, ticker=ticker, timeframe=timeframe, end_ts=candle.end_ts,
                    engine_version=ENGINE_VERSION,
                )
                unchanged = stored_rows and all(
                    row["input_content_hash"] == input_hash for row in stored_rows
                )
                if not unchanged:
                    outcome = reconcile(found, stored_rows)
                    _apply_reconciliation(
                        conn, outcome=outcome, found=found, run_id=run_id, ticker=ticker,
                        timeframe=timeframe, end_ts=candle.end_ts, input_hash=input_hash,
                        superseded_at=data_as_of,
                    )
                    supersessions += len(outcome.superseded) + len(outcome.retracted)
            if not found:
                continue
```

Add `_apply_reconciliation` beside `_scan_one`:

```python
def _apply_reconciliation(
    conn: Connection,
    *,
    outcome: Reconciliation,
    found: Sequence[PatternDetection],
    run_id: UUID,
    ticker: str,
    timeframe: Timeframe,
    end_ts: datetime,
    input_hash: str,
    superseded_at: datetime,
) -> None:
    """Write the supersession facts, resolving replacement ids after the new readings have been stored.

    A replacement id can only be recorded once its row exists, and the replacement rows are written by the
    normal detection path in this same transaction, so the ids are read back here rather than guessed.
    """
    if not outcome.superseded and not outcome.retracted:
        return
    by_hash = {
        row["evidence_hash"]: row["id"]
        for row in active_detections(conn, ticker=ticker, timeframe=timeframe, end_ts=end_ts,
                                     engine_version=ENGINE_VERSION)
    }
    for fact_id, replacement_hash in outcome.superseded:
        replacement_id = by_hash.get(replacement_hash)
        if replacement_id is None:
            continue  # the replacement row is written later in this loop; the next scan records the link
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=fact_id,
            replacement_fact_id=int(replacement_id), reason=SUPERSEDED_BY_REVISION, source_run_id=run_id,
            revision_id=None, superseded_at=superseded_at, input_content_hash=input_hash,
        )
    for fact_id in outcome.retracted:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=fact_id, replacement_fact_id=None,
            reason=NO_LONGER_DETECTED, source_run_id=run_id, revision_id=None,
            superseded_at=superseded_at, input_content_hash=input_hash,
        )
```

> **Ordering matters.** The new readings must be stored *before* `_apply_reconciliation` runs, or a case-A
> replacement id will not exist yet. Move the `record_detection` loop above the reconciliation call, and pass
> `input_content_hash=input_hash` into `record_detection` and `record_candidate`. Add `supersessions` to the
> counters `_scan_one` returns and surface it in `ScanReport` beside `detections` and `candidates`.

- [ ] **Step 6: Add `supersessions` to the scan report**

`_scan_one` returns `(candles, detections, candidates)`. Make it return a fourth counter and add
`supersessions: int` to `ScanReport` beside `detections` and `candidates`, defaulting to `0`, so a scan can be
asked how much it reconciled. Update the existing constructions of `ScanReport` in `run_research_scan`.

- [ ] **Step 7: Write the integration test for the round trip**

Append to `tests/integration/test_research_supersession.py`:

```python
from tests.integration.test_research_scan import (
    CONFIG,
    MARKET_NOW,
    correct_one_minute,
    seed,
)
from tests.integration.support import DAY, PRICE_SOURCE
from tests.support import et
from virtual_orders.research.service import run_research_scan


def test_a_correction_supersedes_the_reading_it_invalidates(engine):
    """The whole point of D96, end to end over real Postgres."""
    seed(engine)
    first = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                              market_now=MARKET_NOW, config=CONFIG)
    assert first.detections > 0
    stored_before = count(engine, "pattern_detections")

    correct_one_minute(engine)
    second = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=et(DAY, "16:45"), config=CONFIG)

    assert second.supersessions >= 1
    assert count(engine, "research_supersessions") >= 1
    assert count(engine, "pattern_detections") >= stored_before   # nothing was deleted or rewritten
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT reason FROM research_supersessions ORDER BY id"
        )).scalars().all()
    assert set(rows) <= {"SUPERSEDED_BY_REVISION", "NO_LONGER_DETECTED"}
```

Import `active_detections`, `FACT_PATTERN_DETECTION` and the reason constants from
`virtual_orders.research.repository` at the top of the file, and `reconcile` plus `Reconciliation` into
`service.py` from `virtual_orders.research.reconcile`.

- [ ] **Step 8: Run everything**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q && uv run ruff check src tests migrations && uv run mypy`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add src/virtual_orders/research/reconcile.py src/virtual_orders/research/service.py \
        tests/research/test_reconcile.py tests/integration/test_research_supersession.py
git commit -m "feat(research): reconcile a revisited bucket against the facts it already produced

The revisit could store a corrected reading beside a stale one but could not say anything when the corrected
candle produced no reading at all, which is exactly the case the Plan 5 canary hit: four buckets that are
complete today and detect nothing, with seven stale rows and no way to mark them.

Reconciliation is a pure decision over what is detected now and what is still active for the bucket, and it
runs only when the bucket's input content genuinely changed, so an identical re-ingestion stays a non-event."
```

---

### Task 7: The active view reaches the API and the dashboard

**Files:**
- Modify: `src/virtual_orders/readmodels/research.py` (`list_detections`, `list_candidates`,
  `pattern_markers`, `candidate_detail`)
- Test: `tests/integration/test_research_supersession.py` (append)

**Interfaces:**
- Consumes: `research_supersessions` (Task 2).
- Produces: every listed read model excludes superseded and retracted facts by default and accepts
  `include_superseded: bool = False`.

A retracted detection that still appears on the chart, in the scanner or in a statistic is the whole defect
this phase exists to close. The audit path keeps `include_superseded=True`.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_research_supersession.py`:

```python
def test_a_retracted_detection_disappears_from_the_read_models(engine):
    detection_id = seed_one_detection(engine)
    with engine.begin() as conn:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=detection_id,
            replacement_fact_id=None, reason=NO_LONGER_DETECTED, source_run_id=uuid4(), revision_id=None,
            superseded_at=datetime(2026, 9, 22, 19, 0, tzinfo=UTC), input_content_hash="corrected",
        )
    with engine.connect() as conn:
        assert list_detections(conn, ticker="AAPL", limit=50) == []
        assert pattern_markers(conn, ticker="AAPL", timeframe=Timeframe.M15,
                               start=WINDOW_START, end=WINDOW_END) == []
        audited = list_detections(conn, ticker="AAPL", limit=50, include_superseded=True)
    assert [row["id"] for row in audited] == [detection_id]
```

Match the real keyword names by reading `src/virtual_orders/readmodels/research.py` first; the call shapes
above follow `src/virtual_orders/api/routes/research.py`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_research_supersession.py -q -k read_models`
Expected: FAIL — the retracted row is still returned

- [ ] **Step 3: Implement**

Add a module-level helper in `src/virtual_orders/readmodels/research.py` and apply it in each listing:

```python
def _not_superseded(query: Select[Any], column: Any, fact_type: str) -> Select[Any]:
    """Exclude facts a later revision superseded or retracted (D96).

    Statistics and screens read the active view; only an explicit audit asks for everything.
    """
    return query.where(column.not_in(
        select(research_supersessions.c.superseded_fact_id)
        .where(research_supersessions.c.fact_type == fact_type)
    ))
```

Give each affected function an `include_superseded: bool = False` keyword and apply `_not_superseded` unless it
is set. `candidate_detail` should return `None` for a superseded candidate under the default.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/integration -q`
Expected: pass

- [ ] **Step 5: Full suite, lint, types, and the recorded contract fixtures**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q && uv run ruff check src tests migrations && uv run mypy`
If a recorded API contract fixture changes, inspect the diff before re-recording: a changed response here means
the active view is doing its job, but a *field* that vanished is a regression.

- [ ] **Step 6: Commit**

```bash
git add src/virtual_orders/readmodels/research.py tests/integration/test_research_supersession.py
git commit -m "feat(research): read models serve the active view

A retracted detection that still shows on the chart or in the scanner is the defect this phase exists to
close, so every listing excludes superseded facts by default and only an explicit audit asks for everything."
```

---

### Task 8: The canary case becomes a portable regression fixture

The seven stale AAPL rows were produced by a real failure — incomplete data, pattern detected, data corrected,
pattern gone. That is worth more than any synthetic fixture, and it must survive a fresh database.

**Files:**
- Create: `tests/fixtures/research/canary_retraction.json`
- Create: the test in `tests/integration/test_research_supersession.py` (append)
- Test: the same file

**Interfaces:**
- Consumes: everything above.
- Produces: a fixture keyed by data, never by database id.

- [ ] **Step 1: Export the fixture from the live canary database**

```bash
docker exec virtual-order-engine-postgres-1 psql -U vo -d vo -At -c "
SELECT json_build_object(
  'detection', json_build_object(
     'ticker', d.ticker, 'timeframe', d.timeframe, 'pattern', d.pattern, 'direction', d.direction,
     'start_ts', d.start_ts, 'end_ts', d.end_ts, 'candles', d.candles,
     'evidence', d.evidence, 'evidence_hash', d.evidence_hash, 'engine_version', d.engine_version),
  'corrected_bars', (
     SELECT json_agg(json_build_object('ts', b.ts, 'open', b.open, 'high', b.high, 'low', b.low,
                                       'close', b.close, 'volume', b.volume) ORDER BY b.ts)
     FROM bars_1m b WHERE b.ticker = d.ticker AND b.ts >= d.start_ts AND b.ts <= d.end_ts)
) FROM pattern_detections d WHERE d.id = 28;" > /tmp/canary_28.json
```

Repeat for ids 17, 24, 29, 39, 40 and 41, and assemble them into
`tests/fixtures/research/canary_retraction.json` as a list of cases. **Do not store the ids** — they belong to
that database. Each case is: the stored detection as data, and the corrected bars that no longer support it.

- [ ] **Step 2: Write the failing test**

```python
def test_the_canary_cases_retract_against_their_corrected_bars(engine):
    """Real regressions: incomplete data produced a reading, corrected data no longer supports it.

    Ids are deliberately absent from the fixture — these must survive a fresh database, a restore and a
    different host.
    """
    for case in load_canary_cases():          # reads the JSON fixture
        detection_id = insert_detection(engine, case["detection"])
        store_bars(engine, case["detection"]["ticker"], case["corrected_bars"])
        report = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                                   market_now=market_now_for(case), config=CONFIG)
        assert report.supersessions >= 1
        with engine.connect() as conn:
            active = active_detections(
                conn, ticker=case["detection"]["ticker"],
                timeframe=Timeframe(case["detection"]["timeframe"]),
                end_ts=parse(case["detection"]["end_ts"]), engine_version="candles-v1",
            )
        assert detection_id not in [row["id"] for row in active]
        assert count(engine, "pattern_detections") >= 1      # nothing was deleted
```

- [ ] **Step 3: Write the fixture helpers**

Add these beside the test, in `tests/integration/test_research_supersession.py`:

```python
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

FIXTURE = Path(__file__).parents[1] / "fixtures" / "research" / "canary_retraction.json"


def load_canary_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def insert_detection(engine, detection: dict) -> int:
    """Replay the stored fact exactly as the pre-fix engine wrote it, ids assigned by this database."""
    with engine.begin() as conn:
        return int(conn.execute(tables.pattern_detections.insert().values(
            run_id=uuid4(), ticker=detection["ticker"], timeframe=detection["timeframe"],
            pattern=detection["pattern"], direction=detection["direction"],
            start_ts=parse(detection["start_ts"]), end_ts=parse(detection["end_ts"]),
            candles=detection["candles"], geometry_score=Decimal("0.5"), context_score=Decimal("0.5"),
            overall_score=Decimal("0.5"), engine_version=detection["engine_version"],
            price_source=PRICE_SOURCE, evidence=detection["evidence"],
            evidence_hash=detection["evidence_hash"], input_content_hash=None,
            data_as_of=parse(detection["end_ts"]), created_at=parse(detection["end_ts"]),
        ).returning(tables.pattern_detections.c.id)).scalar_one())


def store_bars(engine, ticker: str, bars: list[dict]) -> None:
    """The corrected minutes, written through the same backdated path the other integration tests use."""
    backdated_batch(engine, ticker, [
        RawBar(ticker, parse(bar["ts"]), Decimal(str(bar["open"])), Decimal(str(bar["high"])),
               Decimal(str(bar["low"])), Decimal(str(bar["close"])), Decimal(str(bar["volume"])))
        for bar in bars
    ], ingested_at=parse(bars[-1]["ts"]))
    with engine.begin() as conn:
        add_ticker(conn, ticker, added_at=parse(bars[0]["ts"]))


def market_now_for(case: dict) -> datetime:
    """Late enough that the bucket is settled and still inside the revisit window."""
    return parse(case["detection"]["end_ts"]) + timedelta(minutes=30)
```

`input_content_hash=None` is deliberate: these rows predate the column, exactly as the real ones do, and that
is what makes them exercise the "identity unknown, so reconcile" path.

- [ ] **Step 4: Run to verify failure, then make it pass**

Run: `uv run pytest tests/integration/test_research_supersession.py -q -k canary`
Expected first: FAIL — no supersession is recorded.

If a case does **not** retract after the implementation is in place, do not weaken the assertion: find out why.
Either the fixture's bars are not the ones the engine reads for that bucket (check the window and the session),
or reconciliation has a gap. Both are findings worth more than a green test. A case whose corrected bars turn
out to still support the pattern is not a failure either — drop that case from the fixture and say so in the
commit, because it means the original reading was right.

- [ ] **Step 5: Full suite, lint, types**

Run: `uv run pytest -p no:cacheprovider -o addopts="" -q && uv run ruff check src tests migrations && uv run mypy`

- [ ] **Step 6: Verify the frozen core and the locks**

```bash
git diff --exit-code --stat plan/virtual-order-engine-core-complete -- src/core && echo "frozen core clean"
git diff --stat main..HEAD -- uv.lock dashboard/uv.lock pyproject.toml dashboard/pyproject.toml \
        .github/workflows/ci.yml
```
Expected: the first prints "frozen core clean"; the second prints nothing.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/research/canary_retraction.json tests/integration/test_research_supersession.py
git commit -m "test(research): the canary's stale readings become a portable regression fixture

Incomplete data produced a reading, corrected data no longer supports it: seven real cases from the Plan 5
canary, exported as data rather than as database ids so they survive a fresh database, a restore and a
different host."
```

---

## Done when

- `research_supersessions`, `research_datasets` and `research_dataset_revisions` exist, are append-only, and the
  migration round-trips.
- A corrected candle that still yields a reading supersedes the old fact; one that yields nothing retracts it.
- An identical re-ingestion under a new `batch_id` records nothing.
- `active_facts(as_of=X)` reconstructs what was active before a correction.
- `features-v2` gives two scans over identical candles one identity, and the `8b883fb` workaround is gone.
- Every research read model serves the active view by default.
- The seven canary cases retract, from a fixture that carries no database ids.
- Engine and dashboard suites, `ruff`, `mypy`, the frozen-core diff and the dependency locks are all clean.

## Not in this plan

Phase 0B (the spike) and everything after it. In particular: `research_bars`, instruments, universes, the
coverage gate, the 15m backfill, split adjustment, `HistoricalOutcomePolicy` and the empirical statistics.
Phase 0A exists so the spike's mandatory proof can be written; the spike's measurements decide the rest.
