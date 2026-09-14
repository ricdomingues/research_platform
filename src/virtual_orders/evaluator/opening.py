"""Opening job (spec 5.3) with its own run row: sources consulted and failures (Plan 2 close-out entry 10, D16)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine

from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.corporate import apply_dividends, freeze_for_splits, opening_window
from virtual_orders.evaluator.outcomes import OrderOutcome, split_errors
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.sources import DividendSource, SourceDataError, SourceUnavailable, SplitSource

SPLITS_FAILURE_KEY = "splits"


@dataclass(frozen=True)
class OpeningReport:
    run_id: UUID
    session_day: date
    dividends: tuple[OrderOutcome, ...]
    splits: tuple[OrderOutcome, ...]
    source_failures: dict[str, str]


def run_opening(
    engine: Engine,
    *,
    session_day: date,
    primary: DividendSource,
    secondary: DividendSource,
    split_source: SplitSource,
    tolerance: Decimal,
    code_version: str,
    now: datetime,
) -> OpeningReport:
    now = require_aware(now, "now")
    opening_window(session_day, now)  # validated before any run row is created
    base = {
        "session_day": session_day,
        "dividend_sources": [primary.name, secondary.name],
        "split_source": type(split_source).__name__,
    }
    data_as_of = acquire_data_as_of(engine)
    run: RunInfo | None = None
    try:
        with engine.begin() as conn:
            run = start_run(conn, RunKind.OPENING, data_as_of, code_version, detail=base)
        failures: dict[str, str] = {}
        dividends = tuple(apply_dividends(
            engine, ex_date=session_day, primary=primary, secondary=secondary, tolerance=tolerance, now=now,
            source_failures=failures,
        ))
        try:
            splits = tuple(freeze_for_splits(engine, split_source, as_of_day=session_day))
        except (SourceUnavailable, SourceDataError) as exc:
            splits = ()
            failures[SPLITS_FAILURE_KEY] = str(exc)
        dividend_integrity, dividend_errors = split_errors(dividends)
        split_integrity, split_order_errors = split_errors(splits)
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                **base,
                "dividend_orders": len(dividends),
                "split_orders": len(splits),
                "source_failures": failures,
                "dividend_integrity_errors": dividend_integrity,
                "dividend_order_errors": dividend_errors,
                "split_integrity_errors": split_integrity,
                "split_order_errors": split_order_errors,
            })
        return OpeningReport(run.run_id, session_day, dividends, splits, failures)
    except Exception as exc:
        if run is not None:
            with engine.begin() as conn:
                finish_run(conn, run.run_id, RunStatus.FAILED, {**base, "error": repr(exc)})
        raise
