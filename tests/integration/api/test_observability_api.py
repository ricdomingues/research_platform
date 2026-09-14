from datetime import date, datetime

from sqlalchemy import select

from tests.integration.support import CODE_VERSION, DAY, FakeReference, flat_raw, post_json, signal_body
from tests.support import et
from virtual_orders.alerts.outbox import AlertKind, enqueue_alert
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.storage import tables


def get(api, path, **params):
    response = api.client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_health_log_lists_transitions_newest_first(api):
    with api.services.engine.begin() as conn:
        conn.execute(tables.health_state_log.insert().values(state="DEGRADED", cause_codes=["LIVE_CYCLE_STALE"]))
        conn.execute(tables.health_state_log.insert().values(state="HEALTHY", cause_codes=[]))

    entries = get(api, "/health/log")["entries"]
    assert [(e["state"], e["cause_codes"]) for e in entries] == [("HEALTHY", []), ("DEGRADED", ["LIVE_CYCLE_STALE"])]
    assert datetime.fromisoformat(entries[0]["observed_at"]).tzinfo is not None
    assert [e["state"] for e in get(api, "/health/log", limit=1)["entries"]] == ["HEALTHY"]
    assert api.client.get("/health/log", params={"limit": 0}).json()["error"]["code"] == "REQUEST_INVALID"


def test_alert_outbox_lists_delivery_state_without_documents(api):
    engine = api.services.engine
    with engine.begin() as conn:
        for key in ("A", "B", "C"):
            enqueue_alert(conn, alert_key=key, kind=AlertKind.PRICE_CROSS, document={"secret": "document-value"},
                          subject="rule")
        ids = dict(conn.execute(select(tables.alert_outbox.c.alert_key, tables.alert_outbox.c.id)).all())
        conn.execute(tables.alert_delivery_attempts.insert(), [
            {"alert_id": ids["A"], "outcome": "DELIVERED", "status_code": 200, "error_type": None},
            {"alert_id": ids["B"], "outcome": "FAILED", "status_code": None, "error_type": "ConnectTimeout"},
            {"alert_id": ids["B"], "outcome": "EXPIRED", "status_code": None, "error_type": None},
        ])

    response = api.client.get("/alert-outbox")
    alerts = response.json()["alerts"]
    assert [(a["alert_key"], a["kind"], a["last_outcome"], a["failures"]) for a in alerts] == [
        ("C", "PRICE_CROSS", None, 0), ("B", "PRICE_CROSS", "EXPIRED", 1), ("A", "PRICE_CROSS", "DELIVERED", 0),
    ]
    assert "document-value" not in response.text and "document" not in alerts[0]
    for outcome, keys in (("EXPIRED", ["B"]), ("PENDING", ["C"]), ("DELIVERED", ["A"]), ("FAILED", [])):
        assert [a["alert_key"] for a in get(api, "/alert-outbox", outcome=outcome)["alerts"]] == keys, outcome
    invalid = api.client.get("/alert-outbox", params={"outcome": "LOST"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "REQUEST_INVALID"


def test_quality_overview_reports_coverage_and_data_gaps(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    bars = [b for b in flat_raw(DAY, "09:30", "16:00", 105) if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")]
    api.bars.load(bars)  # above the zone: never fills; 35 minutes missing
    run_end_of_day(api.services.engine, gateway=api.services.gateway, reference=FakeReference(),
                   session_day=date(2025, 11, 25), code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    overview = get(api, "/quality/overview")

    assert overview["window_days"] == 14
    assert overview["coverage"] == [{"session_date": "2025-11-25", "orders": 1, "expected_bars": 390,
                                     "missing_bars": 35, "coverage_pct": "91.03"}]
    (gap,) = overview["data_gaps"]
    assert (gap["order_id"], gap["ticker"], gap["minutes"]) == (order_id, "AAPL", 35)
    assert datetime.fromisoformat(gap["gap_start_ts"]) == et(DAY, "10:10")
