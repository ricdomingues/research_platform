from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from core.domain.models import Direction
from virtual_orders.analytics.portfolio import BASIS, OpenPosition, mark_portfolio

MARK_TS = datetime(2025, 11, 25, 15, 29, tzinfo=UTC)


def position(**overrides):
    values = dict(
        order_id=UUID(int=1), ticker="AAPL", direction=Direction.LONG, strategy="REXSHARE", origin="AUTO_STRATEGY",
        status="OPEN", frozen=False, qty_open=Decimal("25"), avg_entry=Decimal("101"), stop_current=Decimal("97"),
        risk_amount=Decimal("100"), realized_pnl=Decimal("0"), costs=Decimal("0"), dividends=Decimal("0"),
        last_close=Decimal("103"), last_close_ts=MARK_TS,
    )
    values.update(overrides)
    return OpenPosition(**values)


def test_long_and_short_positions_are_marked_per_1r_with_allocation():
    short = position(order_id=UUID(int=2), ticker="MSFT", direction=Direction.SHORT, qty_open=Decimal("10"),
                     avg_entry=Decimal("50"), last_close=Decimal("45"), realized_pnl=Decimal("20"),
                     costs=Decimal("1"), dividends=Decimal("-2"))
    portfolio = mark_portfolio([short, position()])

    assert portfolio.basis == BASIS == "PER_1R_NORMALIZED" and "not account money" in portfolio.disclaimer
    assert "gross of exit costs" in portfolio.disclaimer  # M9: marks are not net of the exit that would close them
    rows = [(m.position.ticker, str(m.notional), str(m.unrealized_pnl), str(m.unrealized_r), str(m.open_r),
             str(m.allocation_pct)) for m in portfolio.positions]
    assert rows == [
        ("AAPL", "2575.00", "50.00", "0.5000", "0.5000", "85.1240"),
        ("MSFT", "450.00", "50.00", "0.5000", "0.6700", "14.8760"),  # SHORT: (45-50)*10 mirrored; (20-1-2+50)/100
    ]
    totals = portfolio.totals
    assert (totals.positions, totals.marked, totals.unmarked) == (2, 2, 0)
    assert (str(totals.notional), str(totals.unrealized_pnl), str(totals.unrealized_r), str(totals.risk_amount)) == (
        "3025.00", "100.00", "1.0000", "200.00")


def test_a_position_without_a_stored_close_is_unmarked_and_left_out_of_totals():
    portfolio = mark_portfolio([position(), position(order_id=UUID(int=3), ticker="NVDA", last_close=None,
                                                     last_close_ts=None)])
    nvda = portfolio.positions[1]
    assert nvda.position.ticker == "NVDA"
    assert (nvda.notional, nvda.unrealized_pnl, nvda.unrealized_r, nvda.open_r, nvda.allocation_pct) == (
        None, None, None, None, None)
    assert (portfolio.totals.positions, portfolio.totals.marked, portfolio.totals.unmarked) == (2, 1, 1)
    assert str(portfolio.positions[0].allocation_pct) == "100.0000"
    assert str(portfolio.totals.notional) == "2575.00"


def test_an_empty_portfolio_has_zero_totals():
    totals = mark_portfolio([]).totals
    assert (totals.positions, str(totals.notional), str(totals.unrealized_r)) == (0, "0.00", "0.0000")
