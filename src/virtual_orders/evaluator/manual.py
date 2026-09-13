"""MANUAL_USER orders (spec 3.4.1): actionability as-of the click, coverage-gated by policy and audited."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from core.actionability import ManualOrderRejected, build_manual_order
from core.domain.models import FillConfig, OrderContext, Origin
from core.fills import get_fill_model
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION, signal_context
from virtual_orders.evaluator.coverage import STRICT_PRIMARY_COVERAGE, CoveragePolicy
from virtual_orders.ledger.orders import OrderRow, SignalRow, get_signal
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.ledger.writes import persist_new_order
from virtual_orders.marketdata.asof import (
    acquire_data_as_of,
    bars_in_minutes,
    floor_minute,
    read_bars_as_of,
    selected_data_hash,
)
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable

SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
ACTIONABILITY_UNVERIFIABLE = "ACTIONABILITY_UNVERIFIABLE"
MAX_REPORTED_MISSING = 50

_OUTCOMES = text(
    """
    SELECT latest.detail->>'result' AS result, COUNT(*) AS runs
    FROM evaluation_runs r
    JOIN LATERAL (
        SELECT detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
    ) latest ON true
    WHERE r.kind = 'ACTIONABILITY' AND r.started_at >= :since
    GROUP BY 1
    """
)


class ManualOrderError(Exception):
    def __init__(self, code: str, reason: str | None = None, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code if reason is None else f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.detail: dict[str, Any] = detail or {}


@dataclass(frozen=True)
class ManualOrderCreated:
    order_id: UUID
    actionability_run_id: UUID
    data_as_of: datetime
    partial_bar_skipped: bool


Decision = ManualOrderCreated | ManualOrderError


def _decide(
    conn: Connection,
    run: RunInfo,
    signal: SignalRow,
    ctx: OrderContext,
    config: FillConfig,
    code_version: str,
    decided_at: datetime,
    price_source: str,
    policy: CoveragePolicy,
) -> tuple[Decision, dict[str, Any]]:
    ticker = signal.spec.ticker
    start = signal.evaluation_start_ts
    bar_end = min(floor_minute(decided_at), signal.valid_until_ts)
    minutes = ctx.calendar.expected_minutes(start, bar_end) if bar_end > start else []
    bars = []
    if minutes:
        bars = bars_in_minutes(read_bars_as_of(conn, ticker, price_source, start, bar_end, run.data_as_of), minutes)
    data_hash = selected_data_hash(price_source, ticker, minutes, bars)
    coverage = policy.assess(price_source=price_source, ticker=ticker, expected=minutes, bars=bars)
    audit: dict[str, Any] = {
        "bar_from": minutes[0] if minutes else None,
        "bar_to": minutes[-1] if minutes else None,
        "selected_data_hash": data_hash,
        "coverage_evidence": coverage.evidence,
    }
    # CoverageDecision contract: verified=True must mean no unresolved minutes. A policy that violates this
    # (claims verified with leftover unresolved minutes) is still treated as unverifiable -- never silently
    # let build_manual_order replay primary bars with holes -- and the violation itself is recorded (D7).
    if not coverage.verified or coverage.unresolved:
        unresolved = sorted(coverage.unresolved)
        detail: dict[str, Any] = {
            **audit, "missing_count": len(unresolved), "missing_minutes": unresolved[:MAX_REPORTED_MISSING],
        }
        if coverage.verified and coverage.unresolved:
            detail["policy_contract_violation"] = True
        return ManualOrderError(ACTIONABILITY_UNVERIFIABLE, None, detail), audit

    model = get_fill_model(DEFAULT_FILL_MODEL_VERSION)
    extra = {
        "actionability_run_id": run.run_id,
        "actionability_data_as_of": run.data_as_of,
        "actionability_selected_data_hash": data_hash,
        "actionability_coverage_policy": policy.name,
        "actionability_coverage_evidence": coverage.evidence,
    }
    try:
        built = build_manual_order(model, ctx, bars, decided_at, extra_payload=extra)
    except ManualOrderRejected as rejected:
        decision = rejected.actionability
        code = decision.error_code or SIGNAL_EXPIRED
        reason = None if code == SIGNAL_EXPIRED else decision.reason.value
        return ManualOrderError(code, reason, audit), audit

    order = OrderRow(
        id=uuid4(), signal_id=signal.id, origin=Origin.MANUAL_USER, created_at=decided_at,
        evaluation_start_ts=built.context.evaluation_start_ts, valid_until_ts=built.context.valid_until_ts,
        fill_model_version=DEFAULT_FILL_MODEL_VERSION, config=config, code_version=code_version,
        risk_amount=config.risk_amount, price_source=price_source,
    )
    persist_new_order(conn, order, signal.spec.direction, built.created)
    skipped = bool(built.created.events[0].payload["partial_bar_skipped"])
    return ManualOrderCreated(order.id, run.run_id, run.data_as_of, skipped), audit


def create_manual_order(
    engine: Engine,
    signal_id: UUID,
    *,
    config: FillConfig,
    code_version: str,
    price_source: str,
    created_at: datetime | None = None,
    gateway: MarketDataGateway | None = None,
    coverage_policy: CoveragePolicy = STRICT_PRIMARY_COVERAGE,
) -> ManualOrderCreated:
    with engine.connect() as conn:
        signal = get_signal(conn, signal_id)
    # T is the click, captured once (spec 3.4.1): used unchanged for both the ingest window ceiling and the
    # decision, so a fetch that straddles a minute boundary can never move T or spuriously fail actionability.
    decided_at = require_aware(created_at, "created_at") if created_at is not None else datetime.now(UTC)
    expired = decided_at >= signal.valid_until_ts
    ingest_error: str | None = None
    if gateway is not None and not expired:
        try:
            ingest_bars(engine, gateway.bar_source(price_source), signal.spec.ticker, signal.evaluation_start_ts,
                        min(floor_minute(decided_at), signal.valid_until_ts))
        except UnknownDataSource as exc:
            ingest_error = f"UNKNOWN_DATA_SOURCE: {exc}"
        except (SourceUnavailable, SourceDataError) as exc:
            ingest_error = str(exc)
    data_as_of = acquire_data_as_of(engine)
    ctx = signal_context(signal, config)
    base = {"signal_id": signal.id, "ticker": signal.spec.ticker, "price_source": price_source,
            "coverage_policy": coverage_policy.name}

    with engine.begin() as conn:
        run = start_run(conn, RunKind.ACTIONABILITY, data_as_of, code_version,
                        detail={**base, "created_at": decided_at})
        audit: dict[str, Any] = {}
        outcome: Decision
        if expired:
            # Checked before ingest (never mind a failing provider for a signal that is already past its
            # window): no fetch, no 503 -- an expired signal is always 422 SIGNAL_EXPIRED.
            outcome = ManualOrderError(SIGNAL_EXPIRED)
        elif ingest_error is not None:
            outcome = ManualOrderError(ACTIONABILITY_UNVERIFIABLE, None, {"ingest_error": ingest_error})
        else:
            outcome, audit = _decide(conn, run, signal, ctx, config, code_version, decided_at, price_source,
                                     coverage_policy)
        if isinstance(outcome, ManualOrderError):
            status = RunStatus.FAILED if outcome.code == ACTIONABILITY_UNVERIFIABLE else RunStatus.COMPLETED
            finish_run(conn, run.run_id, status,
                       {**base, "result": outcome.code, "reason": outcome.reason, **outcome.detail})
        else:
            finish_run(conn, run.run_id, RunStatus.COMPLETED,
                       {**base, "result": "ACTIONABLE", "order_id": outcome.order_id, **audit})
    if isinstance(outcome, ManualOrderError):
        raise outcome
    return outcome


def actionability_outcomes(conn: Connection, *, since: datetime) -> dict[str, int]:
    """Observability for D7: how often each actionability result occurs, e.g. ACTIONABILITY_UNVERIFIABLE."""
    return {
        row.result: int(row.runs)
        for row in conn.execute(_OUTCOMES, {"since": since})
        if row.result is not None
    }
