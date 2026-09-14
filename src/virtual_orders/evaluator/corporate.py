"""Opening job pieces (spec 4.7, 5.3): validated dividends and split freezes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from uuid import UUID

from sqlalchemy import Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.dataquality import dividends_agree
from core.domain.models import StepResult
from virtual_orders.evaluator.commands import CommandInput, apply_command, freeze_order
from virtual_orders.evaluator.outcomes import OrderOutcome, isolated
from virtual_orders.ledger.events import known_hashes
from virtual_orders.ledger.runs import last_segment_end
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.sources import (
    DividendRecord,
    DividendSource,
    SourceDataError,
    SourceUnavailable,
    SplitSource,
)
from virtual_orders.storage.tables import dividends

_CANDIDATE_ORDERS = text(
    """
    SELECT o.id AS order_id, g.ticker AS ticker
    FROM orders o LEFT JOIN order_state st ON st.order_id = o.id JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay
      AND (st.order_id IS NULL OR (NOT st.frozen AND st.status IN ('PENDING', 'OPEN', 'PARTIAL')))
      AND o.evaluation_start_ts < :ex_date_open
    ORDER BY g.ticker, o.id
    """
)

_NON_FINAL = text(
    """
    SELECT o.id AS order_id, g.ticker AS ticker, o.evaluation_start_ts AS evaluation_start_ts
    FROM orders o LEFT JOIN order_state st ON st.order_id = o.id JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND (st.order_id IS NULL OR st.status IN ('PENDING', 'OPEN', 'PARTIAL'))
    ORDER BY g.ticker, o.id
    """
)


def _lookup(source: DividendSource, ticker: str, ex_date: date) -> DividendRecord | None:
    try:
        records = source.fetch_dividends(ticker, ex_date, ex_date)
    except (SourceUnavailable, SourceDataError):
        return None
    return next((record for record in records if record.ex_date == ex_date), None)


def _dividend_command(
    ex_date: date, amount: Decimal, validated: bool, tolerance: Decimal, position_confirmed_by: datetime
) -> Callable[[CommandInput], StepResult]:
    event_key = f"DIVIDEND:{ex_date.isoformat()}"
    ref = ex_date.isoformat()

    def command(inp: CommandInput) -> StepResult:
        if event_key in known_hashes(inp.conn, inp.order.id):
            # Already credited for this ex-date (rerun of the opening job): apply_dividend has no
            # per-ex-date memory in OrderState, so re-invoking it would double-count `dividends`
            # even though the duplicate event itself gets deduped at storage. Stay a true no-op.
            return StepResult(inp.projection.state)
        last_to = last_segment_end(inp.conn, inp.order.id)
        if last_to is None or last_to < position_confirmed_by:
            # qty_open as of `now` cannot be trusted unless the order has been evaluated through the
            # close of the session before the ex-date: a later-arriving fill/exit would otherwise be
            # silently missed or double-counted, and rebuild would happily agree with the wrong credit.
            unconfirmed: StepResult = inp.model.flag_review(
                inp.projection.state, "DIVIDEND_POSITION_UNCONFIRMED", ref
            )
            return unconfirmed
        if inp.projection.state.qty_open <= 0:
            return StepResult(inp.projection.state)
        if inp.order.config.dividend_tolerance != tolerance:
            # The stored record's `validated` flag was computed with the job's tolerance; an order
            # configured with a different tolerance cannot trust that verdict either way.
            mismatch: StepResult = inp.model.flag_review(
                inp.projection.state, "DIVIDEND_TOLERANCE_MISMATCH", ref
            )
            return mismatch
        result: StepResult = inp.model.apply_dividend(inp.projection.state, inp.ctx, ex_date, amount, validated)
        return result

    return command


def apply_dividends(
    engine: Engine,
    *,
    ex_date: date,
    primary: DividendSource,
    secondary: DividendSource,
    tolerance: Decimal,
    now: datetime,
) -> list[OrderOutcome]:
    probe = datetime.combine(ex_date, time(12), tzinfo=UTC)
    sessions = calendar_for_window(probe, probe).sessions
    index = next((i for i, s in enumerate(sessions) if s.day == ex_date), None)
    if index is None:
        raise ValueError(f"{ex_date} is not a trading session")
    if index == 0:
        raise ValueError(f"no session loaded before {ex_date}")
    session, previous_session = sessions[index], sessions[index - 1]
    if now < previous_session.close_utc:
        raise ValueError("dividends must be applied at or after the previous session's close")
    if now >= session.open_utc:
        raise ValueError("dividends must be applied before the ex-date session opens")
    position_confirmed_by = previous_session.close_utc - timedelta(minutes=1)

    with engine.connect() as conn:
        by_ticker: dict[str, list[UUID]] = defaultdict(list)
        # An order whose evaluation starts at or after the ex-date open can never hold a position on the ex-date.
        for row in conn.execute(_CANDIDATE_ORDERS, {"ex_date_open": session.open_utc}):
            by_ticker[row.ticker].append(row.order_id)

    outcomes: list[OrderOutcome] = []
    for ticker, order_ids in sorted(by_ticker.items()):
        first, second = _lookup(primary, ticker, ex_date), _lookup(secondary, ticker, ex_date)
        if first is None and second is None:
            continue
        first_amount = None if first is None else first.amount
        second_amount = None if second is None else second.amount
        amount = first_amount if first_amount is not None else second_amount
        if amount is None:
            raise RuntimeError(f"dividend for {ticker} {ex_date} has no amount")
        known = first or second
        with engine.begin() as conn:
            conn.execute(
                pg_insert(dividends)
                .values(
                    ticker=ticker, ex_date=ex_date, amount=amount, pay_date=None if known is None else known.pay_date,
                    sources=[s.name for s, r in ((primary, first), (secondary, second)) if r is not None],
                    validated=dividends_agree(first_amount, second_amount, tolerance),
                    checked_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing(index_elements=["ticker", "ex_date"])
            )
            # First check wins for the credit too: never use this call's own fresh lookups below.
            stored = conn.execute(
                select(dividends.c.amount, dividends.c.validated)
                .where(dividends.c.ticker == ticker, dividends.c.ex_date == ex_date)
            ).one()
        command = _dividend_command(ex_date, stored.amount, stored.validated, tolerance, position_confirmed_by)
        outcomes.extend(
            isolated(order_id, partial(apply_command, engine, order_id, command)) for order_id in order_ids
        )
    return outcomes


def freeze_for_splits(engine: Engine, split_source: SplitSource, *, as_of_day: date) -> list[OrderOutcome]:
    with engine.connect() as conn:
        rows = conn.execute(_NON_FINAL).all()
    if not rows:
        return []
    start = min(row.evaluation_start_ts for row in rows).astimezone(UTC).date()
    splits = split_source.fetch_splits(sorted({row.ticker for row in rows}), start, as_of_day)
    outcomes: list[OrderOutcome] = []
    for row in rows:
        order_start = row.evaluation_start_ts.astimezone(UTC).date()
        for split in sorted(splits, key=lambda s: s.ex_date):
            if split.ticker == row.ticker and order_start <= split.ex_date <= as_of_day:
                freeze = partial(freeze_order, engine, row.order_id, reason="SPLIT", ref=split.ex_date.isoformat())
                outcomes.append(isolated(row.order_id, freeze))
    return outcomes
