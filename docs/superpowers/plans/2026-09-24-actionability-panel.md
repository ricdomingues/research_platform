# Plano 7 — Painel de actionability (WATCH / ACTIONABLE / EXIT)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give the owner a screen that says, in one glance and without lying about freshness, which signals are
still enterable right now, which setups are still blocked, and which open paper positions need mirroring or
review — so a manual decision is made against the engine's own state instead of against a stale table.

**Architecture:** One new read model and one new read-only route. The read model reuses the machinery the
manual-order path already owns — `signal_actionability`, `STRICT_PRIMARY_COVERAGE`, `stored_promotion_errors` —
and writes nothing: no run, no ingest, no gateway call (D103). The dashboard gains a self-refreshing fragment
that renders the answer and nothing else (D105). Freshness is the existing coverage policy, and it always wins
over a green state (D104).

**Tech Stack:** Python 3.12, SQLAlchemy Core (no ORM), FastAPI, Streamlit 1.63 (`st.fragment(run_every=)` is
available), pytest against a real Postgres, `mypy --strict`, `ruff`. No new dependency is permitted.

**Spec:** `docs/superpowers/specs/2026-09-24-actionability-panel-design.md` — read D103, D104 and D105 before
starting.

**Branch:** `plan-7-actionability-panel`, based on `main` at `c592ecf`.

## Global Constraints

- **`src/core` is frozen.** `git diff --exit-code plan/virtual-order-engine-core-complete -- src/core` must stay
  empty. `signal_actionability` is imported as a stable library and never edited.
- **No new external dependency.** `uv.lock`, `dashboard/uv.lock`, both `pyproject.toml` and
  `.github/workflows/ci.yml` must have no diff against `main`.
- **No migration.** This plan adds no table and no column: everything it needs is already stored.
- **The read path writes nothing** (D103). No `start_run`, no `ingest_bars`, no gateway in the new route.
- **Money and measurements are `Decimal`.** Floats appear only at the chart border.
- **Comments, docstrings, identifiers and commit messages are in English.** Documents under `docs/` are in
  Portuguese. Match the surrounding file.
- **Tests run as CI runs them:** `uv run pytest -p no:cacheprovider -o addopts="" -q`, `uv run ruff check src
  tests migrations`, `uv run mypy`. Integration tests need the test Postgres at
  `postgresql+psycopg://vo:vo@127.0.0.1:55432/postgres` (`docker compose -f docker-compose.test.yml up -d`).
  The dashboard suite runs from `dashboard/` with its own `uv run pytest`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/virtual_orders/readmodels/actionability.py` | **New.** The panel's state per signal: actionability, coverage, and the open order's own state. Reads only. |
| `src/virtual_orders/api/routes/signals.py` | Add `GET /signals/actionability`. No writes, no ingest. |
| `src/virtual_orders/readmodels/research.py` | Carry `promotion_blockers` on candidate rows, from the existing policy. |
| `src/virtual_orders/api/routes/research.py` | Serve the blockers the read model now carries. |
| `dashboard/dashboard/client.py` | `signals_actionability()`. |
| `dashboard/dashboard/viewmodels.py` | Pure panel rows: state, colour class, domain reason. Stale-wins lives here and is tested. |
| `dashboard/dashboard/views/terminal.py` | The self-refreshing panel fragment on the Terminal page. |
| `tests/readmodels/test_actionability.py` | **New.** The state machine, including stale-wins. |
| `tests/integration/test_actionability_panel.py` | **New.** The panel and `POST` agree; the read leaves no run behind. |
| `dashboard/tests/test_viewmodels.py` | Panel row shaping and the colour/blink rule. |

---

### Task 1: The panel read model

Build `panel_rows(conn, *, as_of, data_as_of, price_source, limit)` in
`src/virtual_orders/readmodels/actionability.py`.

For every signal whose `valid_until_ts` is within the reporting window, resolve exactly one state:

- Load the signal's stored bars as-of, over the calendar minutes between `evaluation_start_ts` and
  `min(floor_minute(as_of), valid_until_ts)`, with `read_bars_as_of` + `bars_in_minutes`.
- Assess `STRICT_PRIMARY_COVERAGE` over those minutes. Unresolved minutes ⇒ `STALE`, and stop: no other state
  may be computed from holed data (D104).
- Otherwise run `signal_actionability` and map: `ACTIONABLE` ⇒ `ACTIONABLE`; `SIGNAL_EXPIRED` ⇒ `EXPIRED`;
  `INVALIDATED` ⇒ `INVALIDATED`; `STOPPED` / `TARGET_REACHED` / `ENTRY_OPPORTUNITY_ALREADY_OCCURRED` ⇒ `EXIT`
  carrying the domain reason verbatim.
- A real order on that signal overrides the hypothetical for `EXIT`: `needs_review` ⇒ `EXIT` with reason
  `MANUAL_REVIEW_REQUIRED`; a closed order carries its own close reason.

- [ ] 1.1 Write `tests/readmodels/test_actionability.py` first: one case per state, plus a case where a hole in
      the middle of the window makes an otherwise `ACTIONABLE` signal come back `STALE`.
- [ ] 1.2 Implement the read model. It takes a `Connection`, never an `Engine`: no transaction of its own.
- [ ] 1.3 Assert no write: the module must not import `start_run`, `finish_run`, `ingest_bars` or any gateway.

### Task 2: The route

- [ ] 2.1 Add `GET /signals/actionability` to `src/virtual_orders/api/routes/signals.py`, with optional
      `as_of` (aware; naive is `NAIVE_DATETIME`) and a bounded `limit`.
- [ ] 2.2 Respond `{"data_as_of": ..., "as_of": ..., "signals": [...]}`, each row carrying `signal_id`,
      `ticker`, `state`, `reason`, `strategy`, `direction`, `entry_zone_low`, `entry_zone_high`, `stop`,
      `target1`, `target2`, `valid_until_ts` and, when there is one, `order_id`.
- [ ] 2.3 `data_as_of` comes from `acquire_data_as_of`, as every other read route does.

### Task 3: WATCH, from the policy that already exists

- [ ] 3.1 Carry `promotion_blockers` on the rows of `list_candidates`, computed by `stored_promotion_errors`
      with the same evidence the promotion boundary reads. No second implementation of the codes (D105).
- [ ] 3.2 Pin in `tests/research/test_setups.py` that the blockers a listed row reports are the ones the
      promote route would refuse with.

### Task 4: The dashboard panel

- [ ] 4.1 `ApiClient.signals_actionability()`.
- [ ] 4.2 Pure view models: `panel_rows()` shaping state, domain reason and colour class. Stale-wins is decided
      here too, defensively, and tested — the screen must not depend on the backend having been right.
- [ ] 4.3 The fragment: `@st.fragment(run_every="5s")` on the Terminal page, CSS `@keyframes` for the pulse.
      Only `ACTIONABLE` on fresh data pulses. `STALE` renders grey and never pulses, whatever the last state was.
- [ ] 4.4 The panel shows `data_as_of` next to every state, always.

### Task 5: The agreement test

- [ ] 5.1 `tests/integration/test_actionability_panel.py`: against real Postgres, a signal whose window has a
      hole comes back `STALE` from the panel **and** 503 `ACTIONABILITY_UNVERIFIABLE` from
      `POST /signals/{id}/orders`; a signal with full coverage comes back `ACTIONABLE` **and** the `POST`
      succeeds.
- [ ] 5.2 Same file: reading the panel N times adds zero rows to `evaluation_runs` (D103).

### Task 6: Close out

- [ ] 6.1 Full CI locally: engine `pytest`, `ruff`, `mypy`; then the dashboard suite.
- [ ] 6.2 `git diff --exit-code plan/virtual-order-engine-core-complete -- src/core` empty; no diff in the two
      `pyproject.toml`, the two lockfiles or the CI workflow.
- [ ] 6.3 PR against `main`, tag `plan/actionability-panel-complete` after merge, closeout note under
      `docs/superpowers/notes/`.
