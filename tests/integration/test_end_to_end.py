"""Recorded session through the real Alpaca adapter: signal -> orders -> events -> projection -> metrics -> REPRODUCE."""

import json
from datetime import date, datetime

import httpx
from sqlalchemy import text

from core.domain.models import Direction, FillConfig
from core.metrics.summary import INSUFFICIENT_SAMPLE, TradeResult, execution_counts, summarize
from tests.integration.support import CODE_VERSION, DAY, SIGNAL_CREATED_AT, FakeReference, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.rebuild import rebuild_all_projections
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.ledger.events import stored_events
from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaBars
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.storage.codec import state_from_document

PAGE_SIZE = 100


def recorded_alpaca() -> AlpacaBars:
    ordered = sorted(scenario_bars(), key=lambda b: b.ts)

    def handler(request: httpx.Request) -> httpx.Response:
        start = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00"))
        window = [b for b in ordered if start <= b.ts <= end]
        offset = int(request.url.params.get("page_token") or 0)
        page = window[offset : offset + PAGE_SIZE]
        token = str(offset + PAGE_SIZE) if offset + PAGE_SIZE < len(window) else None
        body = {
            "bars": [
                {"t": b.ts.isoformat().replace("+00:00", "Z"), "o": float(b.open), "h": float(b.high),
                 "l": float(b.low), "c": float(b.close), "v": int(b.volume), "n": 1, "vw": float(b.close)}
                for b in page
            ],
            "symbol": "AAPL",
            "next_page_token": token,
        }
        return httpx.Response(200, text=json.dumps(body))

    return AlpacaBars(httpx.Client(transport=httpx.MockTransport(handler)), "key", "secret", sleep=lambda s: None)


def trades(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT st.order_id, g.direction, st.opened_at, st.closed_at, st.r_multiple, st.mfe_r, st.mae_r,
                   st.state_document
            FROM order_state st JOIN orders o ON o.id = st.order_id JOIN signals g ON g.id = o.signal_id
            WHERE NOT o.replay
            """
        )).all()
    closed = [
        TradeResult(str(r.order_id), Direction(r.direction), r.opened_at, r.closed_at, r.r_multiple, r.mfe_r,
                    r.mae_r, tuple(r.state_document["review_reasons"]))
        for r in rows if r.state_document["status"] == "CLOSED"
    ]
    return closed, [state_from_document(r.state_document) for r in rows]


def test_recorded_session_end_to_end(engine):
    gateway = MarketDataGateway([recorded_alpaca()])  # the only place a concrete provider is chosen
    submission = submit_signal(engine, signal_body(), config=FillConfig(), code_version=CODE_VERSION,
                               price_source=ALPACA_IEX_SOURCE, now=SIGNAL_CREATED_AT)
    manual = create_manual_order(engine, submission.signal_id, config=FillConfig(), code_version=CODE_VERSION,
                                 price_source=ALPACA_IEX_SOURCE, created_at=et(DAY, "10:00", 30), gateway=gateway)
    for hm in ("10:30", "11:30", "13:00"):
        report = run_live_cycle(engine, gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
        assert report.ingest_failures == {} and all(o.error is None for o in report.outcomes)
    eod = run_end_of_day(engine, gateway=gateway, reference=FakeReference(), session_day=date(2025, 11, 25),
                         code_version=CODE_VERSION, market_now=et(DAY, "16:30"))
    assert eod.expired == ()

    with engine.connect() as conn:
        auto_events = [e.prepared for e in stored_events(conn, submission.auto_order_id)]
        manual_events = [e.prepared for e in stored_events(conn, manual.order_id)]
    assert [e.type for e in auto_events] == ["ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
    assert [e.type for e in manual_events] == ["ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
    assert auto_events[-1].payload["expected_bars"] == 201
    assert manual_events[-1].payload["expected_bars"] == 170
    assert manual_events[0].payload["partial_bar_skipped"] is True
    with engine.connect() as conn:
        providers = conn.execute(text("SELECT DISTINCT provider FROM bar_batches")).scalars().all()
        sources = conn.execute(text("SELECT DISTINCT source FROM bars_1m")).scalars().all()
    assert providers == ["alpaca"] and sources == [ALPACA_IEX_SOURCE]

    closed, states = trades(engine)
    summary = summarize(closed, execution_counts(states), resamples=200, seed=42)
    assert summary.trades == 2 and summary.win_rate == 1.0 and summary.expectancy_r == 1.75
    assert summary.execution_rate == 1.0 and INSUFFICIENT_SAMPLE in summary.warnings

    replay = reproduce_orders(engine, code_version="e2e-replay", created_from=et(DAY, "08:00"),
                              created_to=et(DAY, "23:00"))
    assert replay.failures == {} and len(replay.created) == 2
    with engine.connect() as conn:
        for source_id, replay_id in replay.created.items():
            assert [e.prepared.identity() for e in stored_events(conn, replay_id)] == [
                e.prepared.identity() for e in stored_events(conn, source_id)
            ]
    assert set(rebuild_all_projections(engine).values()) == {"OK"}
