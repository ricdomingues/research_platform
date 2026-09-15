from datetime import date
from decimal import Decimal
from uuid import uuid4

from tests.integration.observation_support import recorded_run, window_for
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeReference,
    feeds,
    flat_raw,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.ledger.runs import RunKind, RunStatus
from virtual_orders.readmodels.observation_operations import (
    ACTIONABILITY_UNVERIFIABLE,
    RUN_KIND_ORDER,
    ActionabilitySection,
    AlertDeliverySection,
    DataQualitySection,
    FeedFailures,
    HealthSection,
    RecheckSection,
    actionability,
    alert_deliveries,
    data_quality,
    health_transitions,
    provider_failures,
    recheck_activity,
    window_runs,
    worker_activity,
)
from virtual_orders.readmodels.observation_window import session_window
from virtual_orders.storage import tables

SESSION = date(2025, 11, 25)
NEXT = "2025-11-26"
COMPLETED, FAILED = RunStatus.COMPLETED, RunStatus.FAILED


def test_provider_failures_are_counted_by_run_kind_with_codes_and_never_messages(engine):
    for hm, failures in (
        ("10:00", {"fake_feed:AAPL": "timeout talking to https://secret-host"}),
        ("10:02", {"fake_feed:AAPL": "timeout", "fake_feed:MSFT": "UNKNOWN_DATA_SOURCE: nope"}),
        ("10:04", {"fake_feed:AAPL": "timeout"}),
    ):
        recorded_run(engine, RunKind.LIVE, start_detail={}, status=COMPLETED,
                     detail={"orders": 1, "ingest_failures": failures, "market_now": et(DAY, hm)})
    recorded_run(engine, RunKind.LIVE, start_detail={}, status=FAILED,
                 detail={"error": "OperationalError('password=hunter2')", "market_now": et(DAY, "10:06")})
    recorded_run(engine, RunKind.LIVE, start_detail={}, status=FAILED,  # no "Type(" prefix: never the text itself
                 detail={"error": "password=hunter2 host=db", "market_now": et(DAY, "10:07")})
    for hm, stored in (("10:08", "db.internal(host)"), ("10:09", "password(hunter2)")):  # dotted or lowercase: UNKNOWN
        recorded_run(engine, RunKind.LIVE, start_detail={}, status=FAILED,
                     detail={"error": stored, "market_now": et(DAY, hm)})
    recorded_run(engine, RunKind.LIVE, start_detail={}, status=COMPLETED,
                 detail={"ingest_failures": {"fake_feed:AAPL": "x"}, "market_now": et(NEXT, "10:00")})
    recorded_run(engine, RunKind.WATCHLIST, start_detail={"market_now": et(DAY, "10:00")}, status=COMPLETED,
                 detail={"ingest_failures": {"fake_feed:NVDA": "ERROR:KeyError"}, "market_now": et(DAY, "10:00")})
    recorded_run(engine, RunKind.OPENING, start_detail={"session_day": DAY}, status=COMPLETED,
                 detail={"session_day": DAY, "source_failures": {"fmp": "HTTP 429"}})
    recorded_run(engine, RunKind.END_OF_DAY, start_detail={"session_day": DAY}, status=COMPLETED,
                 detail={"session_day": DAY, "unavailable": {"AAPL:1m": "yfinance down"}, "not_evaluated": {}})

    with engine.connect() as conn:
        section = provider_failures(conn, window_for(engine, SESSION))

    by_kind = {item.run_kind: item for item in section.by_run_kind}
    assert list(by_kind) == ["LIVE", "WATCHLIST", "OPENING", "END_OF_DAY"]
    assert by_kind["LIVE"] == FeedFailures(
        run_kind="LIVE", runs=7, failed_runs=4, runs_with_failures=3, failures=4,
        codes={"SOURCE_ERROR": 3, "UNKNOWN_DATA_SOURCE": 1}, feeds=("fake_feed:AAPL", "fake_feed:MSFT"),
        feeds_total=2, failed_run_errors={"OperationalError": 1, "UNKNOWN": 3},
    )
    assert by_kind["WATCHLIST"].codes == {"UNEXPECTED_ERROR": 1}
    assert by_kind["OPENING"].feeds == ("fmp",) and by_kind["END_OF_DAY"].feeds == ("AAPL:1m",)
    assert section.total_failures == 7 and section.consecutive_live_max == 3
    for leaked in ("secret-host", "hunter2", "host=db", "password", "db.internal", "internal", "429"):
        assert leaked not in repr(section), leaked
    with engine.connect() as conn:  # the report assembly passes one scan of every run kind: same result
        window = window_for(engine, SESSION)
        assert provider_failures(conn, window, runs=window_runs(conn, window, RUN_KIND_ORDER)) == section


def test_actionability_answers_are_counted_with_the_503_causes(engine):
    base = {"signal_id": str(uuid4()), "ticker": "AAPL", "price_source": "fake_feed",
            "coverage_policy": "STRICT_PRIMARY_COVERAGE"}

    def click(day, hm, status, **final):
        recorded_run(engine, RunKind.ACTIONABILITY, start_detail={**base, "created_at": et(day, hm)}, status=status,
                     detail={**base, "reason": None, **final})

    click(DAY, "10:00", COMPLETED, result="ACTIONABLE", order_id=str(uuid4()))
    click(DAY, "10:01", FAILED, result="ACTIONABILITY_UNVERIFIABLE", ingest_error="SourceUnavailable: host")
    click(DAY, "10:02", FAILED, result="ACTIONABILITY_UNVERIFIABLE", missing_count=3, missing_minutes=[])
    click(DAY, "10:03", FAILED, result="ACTIONABILITY_UNVERIFIABLE", missing_count=1, missing_minutes=[],
          policy_contract_violation=True)
    click(DAY, "10:04", COMPLETED, result="SIGNAL_EXPIRED")
    click(DAY, "10:05", None)  # no final status as of the report
    click(NEXT, "10:00", FAILED, result="ACTIONABILITY_UNVERIFIABLE", ingest_error="x")

    with engine.connect() as conn:
        section = actionability(conn, window_for(engine, SESSION))

    assert section == ActionabilitySection(
        requests=6, results={"ACTIONABILITY_UNVERIFIABLE": 3, "ACTIONABLE": 1, "SIGNAL_EXPIRED": 1}, unverifiable=3,
        unverifiable_causes={"MISSING_MINUTES": 1, "POLICY_CONTRACT_VIOLATION": 1, "PROVIDER_FAILURE": 1},
        unverifiable_rate=Decimal("0.5000"), unfinished=1,
    )


def test_missing_bars_gaps_and_unevaluated_orders_come_from_the_session_quality(engine):
    submit_default(engine)
    submit_default(engine, client_signal_id="msft", ticker="MSFT")
    source = FakeBarSource([b for b in flat_raw(DAY, "09:30", "16:00", 105)
                            if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")])
    source.failing.add("MSFT")
    run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=SESSION,
                   code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    with engine.connect() as conn:
        section = data_quality(conn, window_for(engine, SESSION))
        next_day = data_quality(conn, window_for(engine, date(2025, 11, 26)))

    assert section == DataQualitySection(
        orders_measured=1, expected_bars=390, missing_bars=35, coverage_pct=Decimal("91.03"), orders_with_missing=1,
        gaps=1, gap_minutes=35, not_evaluated={"PROVIDER_FAILURE": 1},
    )
    assert (next_day.orders_measured, next_day.gaps, next_day.not_evaluated) == (0, 0, {})


def test_recheck_rows_are_attributed_to_the_recheck_runs_of_the_window(engine):
    order_id = submit_default(engine).auto_order_id
    in_window = recorded_run(
        engine, RunKind.QUALITY_RECHECK, start_detail={"sessions": ["2025-11-21"], "market_now": et(DAY, "16:30")},
        status=COMPLETED, detail={"market_now": et(DAY, "16:30"),
                                  "not_evaluated": {f"{order_id}:2025-11-20": "PROVIDER_FAILURE",
                                                    f"{order_id}:2025-11-19": "ALREADY_RECHECKED"}},
    )
    later = recorded_run(engine, RunKind.QUALITY_RECHECK, start_detail={"market_now": et(NEXT, "16:30")},
                         status=COMPLETED, detail={"market_now": et(NEXT, "16:30"), "not_evaluated": {}})
    with engine.begin() as conn:
        for session_date, run_id, payload in (
            (date(2025, 11, 24), in_window, {"status": "EVALUATED"}),
            (date(2025, 11, 21), in_window,
             {"status": "PROVIDER_FAILURE_FINAL", "terminal_reason": "PROVIDER_FAILURE"}),
            (SESSION, later, {"status": "EVALUATED"}),
        ):
            conn.execute(tables.data_quality_rechecks.insert().values(
                order_id=order_id, session_date=session_date, recheck_key=f"DATA_QUALITY_RECHECK:{session_date}:x",
                run_id=run_id, source_run_id=run_id, data_as_of=et(DAY, "16:30"), payload=payload,
            ))

    with engine.connect() as conn:
        section = recheck_activity(conn, window_for(engine, SESSION))

    assert section == RecheckSection(
        runs=1, rows=2, statuses={"EVALUATED": 1, "PROVIDER_FAILURE_FINAL": 1},
        terminal_reasons={"PROVIDER_FAILURE": 1}, sessions=(date(2025, 11, 21), date(2025, 11, 24)),
        about_this_session={"EVALUATED": 1}, not_evaluated={"PROVIDER_FAILURE": 1}, skipped={"ALREADY_RECHECKED": 1},
    )


def test_alert_deliveries_are_counted_by_the_database_clock_inside_the_window(engine):
    with engine.begin() as conn:
        ids = {}
        for key, kind, created in (("A", "ORDER_EVENT", et(DAY, "10:00")), ("B", "HEALTH", et(DAY, "11:00")),
                                   ("C", "PRICE_CROSS", et(DAY, "12:00")), ("D", "HEALTH", et(NEXT, "10:00"))):
            ids[key] = conn.execute(tables.alert_outbox.insert().values(
                alert_key=key, kind=kind, document={"secret": "document-value"}, created_at=created,
            ).returning(tables.alert_outbox.c.id)).scalar_one()
        conn.execute(tables.alert_delivery_attempts.insert(), [
            {"alert_id": ids["A"], "outcome": "FAILED", "status_code": 502, "error_type": "AlertDeliveryFailed",
             "attempted_at": et(DAY, "10:01")},
            {"alert_id": ids["A"], "outcome": "DELIVERED", "status_code": 200, "error_type": None,
             "attempted_at": et(DAY, "10:03")},
            {"alert_id": ids["B"], "outcome": "FAILED", "status_code": None, "error_type": "ConnectTimeout",
             "attempted_at": et(DAY, "11:01")},
            {"alert_id": ids["B"], "outcome": "FAILED", "status_code": None, "error_type": "ConnectTimeout",
             "attempted_at": et(DAY, "11:03")},
            {"alert_id": ids["D"], "outcome": "EXPIRED", "status_code": None, "error_type": None,
             "attempted_at": et(NEXT, "11:00")},
        ])

    with engine.connect() as conn:
        section = alert_deliveries(conn, window_for(engine, SESSION))

    assert section == AlertDeliverySection(
        created={"HEALTH": 1, "ORDER_EVENT": 1, "PRICE_CROSS": 1}, attempts={"DELIVERED": 1, "FAILED": 3},
        failed_alerts=2, failure_types={"ConnectTimeout": 2, "HTTP_502": 1}, pending_at_end=2,
    )
    assert "document-value" not in repr(section)


def worker_row(conn, session_id, event, at, **fields):
    conn.execute(tables.worker_sessions.insert().values(session_id=session_id, event=event, recorded_at=at, **fields))


def test_worker_restarts_unclean_ends_and_open_sessions(engine):
    s0, s1, s2, s3, s4 = (uuid4() for _ in range(5))
    with engine.begin() as conn:
        worker_row(conn, s0, "STARTED", et("2025-11-24", "08:00"), code_version="sha-a")
        worker_row(conn, s0, "STOPPED", et("2025-11-24", "20:00"), exit_code=0, reason="SIGNAL")
        worker_row(conn, s1, "STARTED", et(DAY, "08:00"), code_version="sha-b")  # clean restart after s0
        worker_row(conn, s2, "STARTED", et(DAY, "12:00"), code_version="sha-b")  # s1 has no stop row: unclean
        worker_row(conn, s3, "STARTED", et(DAY, "13:05"), code_version="sha-b")  # took the lock s2 lost
        worker_row(conn, s2, "STOPPED", et(DAY, "13:06"), exit_code=3, reason="LOCK_LOST")  # after s3: still clean
        worker_row(conn, s4, "STARTED", et(NEXT, "08:00"), code_version="sha-c")  # s3 has no stop row

    with engine.connect() as conn:
        section = worker_activity(conn, window_for(engine, SESSION))
        first_ever = worker_activity(conn, window_for(engine, date(2025, 11, 24)))
        next_day = worker_activity(conn, window_for(engine, date(2025, 11, 26)))

    assert (section.starts, section.restarts, section.unclean_ends) == (3, 3, 1)
    assert (section.stops, section.exit_codes) == ({"LOCK_LOST": 1}, {"3": 1})
    # s3 has no stop row at the window end: open (running, or ended without a stop row), never a restart here
    assert section.code_versions == ("sha-b",) and section.open_session_at_end is True
    assert [(view.session_id, view.exit_code, view.reason) for view in section.sessions] == [
        (s1, None, None), (s2, 3, "LOCK_LOST"), (s3, None, None)]
    assert (first_ever.starts, first_ever.restarts, first_ever.unclean_ends) == (1, 0, 0)
    assert first_ever.stops == {"SIGNAL": 1} and first_ever.open_session_at_end is False
    # s3's unclean end is attributed to the window of the next start (s4); s4 itself is still open
    assert (next_day.starts, next_day.restarts, next_day.unclean_ends, next_day.open_session_at_end) == (1, 1, 1, True)
    assert next_day.stops == {} and next_day.code_versions == ("sha-c",)


def insert_health(conn, state, codes, at):
    conn.execute(tables.health_state_log.insert().values(state=state, cause_codes=codes, observed_at=at))


def test_health_transitions_and_time_in_each_state(engine):
    with engine.begin() as conn:
        insert_health(conn, "HEALTHY", [], et("2025-11-24", "15:00"))
        insert_health(conn, "DEGRADED", ["LIVE_CYCLE_STALE"], et(DAY, "10:00"))
        insert_health(conn, "DEGRADED", ["INGEST_FAILURES_CONSECUTIVE", "LIVE_CYCLE_STALE"], et(DAY, "10:30"))
        insert_health(conn, "HEALTHY", [], et(DAY, "11:00"))
        insert_health(conn, "DEGRADED", ["LIVE_CYCLE_STALE"], et(NEXT, "09:00"))

    with engine.connect() as conn:
        section = health_transitions(conn, window_for(engine, SESSION))
        early = health_transitions(conn, session_window(SESSION, et(DAY, "10:15")))  # as of 10:15 ET

    # Three log rows, two state changes: DEGRADED -> DEGRADED (cause codes only) is a log row, not a transition.
    assert section == HealthSection(
        state_at_start="HEALTHY", transitions=2, log_rows=3, entered={"DEGRADED": 1, "HEALTHY": 1},
        cause_codes={"INGEST_FAILURES_CONSECUTIVE": 1, "LIVE_CYCLE_STALE": 2},
        seconds_by_state={"DEGRADED": 3600, "HEALTHY": 82800}, state_at_end="HEALTHY",
    )
    assert (early.transitions, early.log_rows, early.seconds_by_state, early.state_at_end) == (
        1, 1, {"DEGRADED": 900, "HEALTHY": 36000}, "DEGRADED")


def test_the_local_actionability_unverifiable_constant_mirrors_evaluator_manual() -> None:
    # Plan 4 fix round 1: observation_operations no longer imports virtual_orders.evaluator.manual (reverse
    # boundary); this pins its local copy of the code string to the source of truth. Tests are not scanned.
    from virtual_orders.evaluator.manual import ACTIONABILITY_UNVERIFIABLE as EVALUATOR_ACTIONABILITY_UNVERIFIABLE

    assert ACTIONABILITY_UNVERIFIABLE == EVALUATOR_ACTIONABILITY_UNVERIFIABLE
