"""Virtual portfolio marking (D45). Pure: positions are sized per 1R of risk, never account money (spec 1.2 item 2)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Direction

BASIS = "PER_1R_NORMALIZED"
DISCLAIMER = (
    "Virtual positions sized per 1R of risk (risk_amount), not account money: no cash, limits or overlap "
    "constraints (that is the Portfolio Simulator, a separate sub-project). Unrealized P&L is gross of exit costs "
    "and stop slippage, marked at the last stored close, which can belong to an earlier session."
)
MONEY_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class OpenPosition:
    order_id: UUID
    ticker: str
    direction: Direction
    strategy: str
    origin: str
    status: str
    frozen: bool
    qty_open: Decimal
    avg_entry: Decimal
    stop_current: Decimal | None
    risk_amount: Decimal
    realized_pnl: Decimal
    costs: Decimal
    dividends: Decimal
    last_close: Decimal | None
    last_close_ts: datetime | None


@dataclass(frozen=True)
class MarkedPosition:
    position: OpenPosition
    notional: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_r: Decimal | None
    open_r: Decimal | None
    allocation_pct: Decimal | None


@dataclass(frozen=True)
class PortfolioTotals:
    positions: int
    marked: int
    unmarked: int
    notional: Decimal
    unrealized_pnl: Decimal
    unrealized_r: Decimal
    risk_amount: Decimal


@dataclass(frozen=True)
class VirtualPortfolio:
    basis: str
    disclaimer: str
    positions: tuple[MarkedPosition, ...]
    totals: PortfolioTotals


def _quantized(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        result = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return result if result != 0 else ZERO.quantize(quantum)  # never "-0.00"


def mark_portfolio(positions: Sequence[OpenPosition]) -> VirtualPortfolio:
    ordered = sorted(positions, key=lambda item: (item.ticker, str(item.order_id)))
    marked: list[MarkedPosition] = []
    with localcontext(CANONICAL_CONTEXT):
        notional_total = sum((item.last_close * item.qty_open for item in ordered if item.last_close is not None), ZERO)
        unrealized_total = r_total = risk_total = ZERO
        for item in ordered:
            if item.last_close is None:
                marked.append(MarkedPosition(item, None, None, None, None, None))
                continue
            move = item.last_close - item.avg_entry
            pnl = move * item.qty_open if item.direction is Direction.LONG else -move * item.qty_open
            notional = item.last_close * item.qty_open
            unrealized_r = pnl / item.risk_amount
            open_r = (item.realized_pnl - item.costs + item.dividends + pnl) / item.risk_amount
            allocation = ZERO if notional_total == 0 else notional / notional_total * HUNDRED
            unrealized_total += pnl
            r_total += unrealized_r
            risk_total += item.risk_amount
            marked.append(MarkedPosition(
                item, _quantized(notional, MONEY_QUANTUM), _quantized(pnl, MONEY_QUANTUM),
                _quantized(unrealized_r, RATIO_QUANTUM), _quantized(open_r, RATIO_QUANTUM),
                _quantized(allocation, RATIO_QUANTUM),
            ))
    count = sum(1 for item in marked if item.notional is not None)
    totals = PortfolioTotals(
        len(marked), count, len(marked) - count, _quantized(notional_total, MONEY_QUANTUM),
        _quantized(unrealized_total, MONEY_QUANTUM), _quantized(r_total, RATIO_QUANTUM),
        _quantized(risk_total, MONEY_QUANTUM),
    )
    return VirtualPortfolio(BASIS, DISCLAIMER, tuple(marked), totals)
