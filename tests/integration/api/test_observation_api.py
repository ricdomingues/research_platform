from datetime import datetime
from uuid import UUID

from tests.integration.support import DAY, count, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.worker.jobs import JobResult, WorkerJobs

NEXT = "2025-11-26"
ERROR = "OBSERVATION_REQUEST_INVALID"
HISTORY = ("evaluation_runs", "order_events", "bar_batches", "alert_outbox", "worker_sessions")


def recorded_session(api) -> UUID:
    """AUTO and MANUAL orders fill at 10:05 and close at 12:50 (r = 1.75); REPRODUCE replays both."""
    api.bars.load(scenario_bars())
    created = post_json(api.client, "/signals", signal_body())
    assert created.status_code == 201, created.text
    api.clock.set(et(DAY, "10:00", 30))
    assert api.client.post(f"/signals/{created.json()['signal_id']}/orders").status_code == 201
    jobs = WorkerJobs(api.services)
    for hm in ("10:30", "11:30", "13:00"):
        api.clock.set(et(DAY, hm))
        assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    replay = post_json(api.client, "/replay", {"mode": "REPRODUCE", "from": et(DAY, "08:00").isoformat(),
                                               "to": et(DAY, "23:00").isoformat()})
    assert replay.status_code == 200, replay.text
    engine = api.services.engine
    with engine.begin() as conn:  # a failed cycle whose stored message carries a secret
        run = start_run(conn, RunKind.LIVE, acquire_data_as_of(engine), "test-sha")
        finish_run(conn, run.run_id, RunStatus.FAILED,
                   {"error": "OperationalError('password=hunter2 host=db.internal')", "market_now": et(DAY, "13:02")})
    return UUID(created.json()["auto_order_id"])


def test_the_report_is_read_from_stored_data_only_and_labels_the_estimate(api):
    auto_id = recorded_session(api)
    calls, before = len(api.bars.calls), {name: count(api.services.engine, name) for name in HISTORY}

    response = api.client.get("/observation/report", params={"day": DAY})

    assert response.status_code == 200, response.text
    assert len(api.bars.calls) == calls  # no provider call (D60)
    assert {name: count(api.services.engine, name) for name in HISTORY} == before  # no write (D60)
    body = response.json()
    assert (body["report_version"], body["session_day"], body["complete"]) == ("OBSERVATION_REPORT_V1", DAY, True)
    assert datetime.fromisoformat(body["as_of"]).tzinfo is not None
    trades = body["trades"]
    assert (trades["closed"], trades["replay_closed"], trades["filled"]) == (2, 2, 2)  # replay never mixed in
    assert (trades["stats"]["trades"], trades["stats"]["sum_r"], trades["stats"]["mean_r"]) == (2, "3.5", "1.75")
    assert {row["origin"] for row in trades["rows"]} == {"AUTO_STRATEGY", "MANUAL_USER"}
    assert str(auto_id) in {row["order_id"] for row in trades["rows"]}
    assert body["actionability"]["results"] == {"ACTIONABLE": 1} and body["actionability"]["unverifiable"] == 0
    assert body["latency"]["bar"] == {"count": 2, "median_seconds": 3900, "p90_seconds": 3900, "max_seconds": 3900}
    pressure = body["pressure"]
    assert (pressure["estimate"], pressure["method"], pressure["disclaimer"]) == (True, METHOD, DISCLAIMER)
    assert "not causal" in pressure["association_note"]
    live = next(item for item in body["provider_failures"]["by_run_kind"] if item["run_kind"] == "LIVE")
    assert live["failed_run_errors"] == {"OperationalError": 1}
    assert "hunter2" not in response.text and "db.internal" not in response.text


def test_invalid_report_days_use_fixed_codes(api):
    cases = [
        ({"day": "2025-11-29"}, 422, ERROR, ["NOT_A_SESSION:2025-11-29"]),
        ({"day": "2025-11-27"}, 422, ERROR, ["NOT_A_SESSION:2025-11-27"]),
        ({"day": "2099-01-05"}, 422, ERROR, ["DAY_IN_FUTURE:2099-01-05"]),
    ]
    for params, status, code, errors in cases:
        response = api.client.get("/observation/report", params=params)
        assert response.status_code == status, params
        assert response.json() == {"error": {"code": code, "reason": None, "detail": {"errors": errors}}}
    for params in ({"day": "25/11/2025"}, {}):
        response = api.client.get("/observation/report", params=params)
        assert response.status_code == 422 and response.json()["error"]["code"] == "REQUEST_INVALID"


def test_the_summary_covers_every_session_of_the_range(api):
    recorded_session(api)

    response = api.client.get("/observation/summary", params={"from": "2025-11-22", "to": NEXT})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["first_day"], body["last_day"], body["sessions"]) == ("2025-11-24", NEXT, 3)
    assert [(day["session_day"], day["trades"]) for day in body["days"]] == [
        ("2025-11-24", 0), (DAY, 2), (NEXT, 0)]
    assert body["trades"]["sum_r"] == "3.5" and body["pressure"]["estimate"] is True
    assert "hunter2" not in response.text


def test_invalid_summary_ranges_use_fixed_codes(api):
    for params, errors in (
        ({"from": NEXT, "to": DAY}, ["EMPTY_RANGE"]),
        ({"from": "2025-09-01", "to": DAY}, ["RANGE_TOO_LARGE"]),
        ({"from": "2025-11-29", "to": "2025-11-30"}, ["NO_SESSIONS"]),
        ({"from": DAY, "to": "2099-01-05"}, ["RANGE_TOO_LARGE", "DAY_IN_FUTURE:2099-01-05"]),
    ):
        response = api.client.get("/observation/summary", params=params)
        assert response.status_code == 422, params
        assert response.json() == {"error": {"code": ERROR, "reason": None, "detail": {"errors": errors}}}
    missing = api.client.get("/observation/summary", params={"from": DAY})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "REQUEST_INVALID"
