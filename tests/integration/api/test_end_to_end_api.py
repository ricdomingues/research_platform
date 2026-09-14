"""Spec 7 end to end through the HTTP API (D54): recorded session (fake feed) -> signal -> AUTO and MANUAL orders ->
events -> projection -> portfolio -> metrics -> REPRODUCE identical. Real jobs, controlled clock, no network, no sleep."""

from uuid import UUID

from tests.integration.support import DAY, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.worker.jobs import JobResult, WorkerJobs


def get(api, path, **params):
    response = api.client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def identities(api, order_id):
    return [(e["event_key"], e["payload_hash"]) for e in get(api, f"/orders/{order_id}")["events"]]


def test_recorded_session_end_to_end_through_the_api(api):
    jobs = WorkerJobs(api.services)
    api.bars.load(scenario_bars())  # fill 10:05 @101, TARGET1 11:00 @106, TARGET2 12:50 @110 -> r = 1.75

    created = post_json(api.client, "/signals", signal_body())
    assert created.status_code == 201, created.text
    signal_id, auto_id = created.json()["signal_id"], UUID(created.json()["auto_order_id"])
    assert [(s["signal_id"], s["auto_order_status"]) for s in get(api, "/signals", date=DAY)["signals"]] == [
        (signal_id, "PENDING")]

    api.clock.set(et(DAY, "10:00", 30))
    manual = api.client.post(f"/signals/{signal_id}/orders")
    assert manual.status_code == 201, manual.text
    assert manual.json()["partial_bar_skipped"] is True
    manual_id = UUID(manual.json()["order_id"])

    api.clock.set(et(DAY, "10:30"))
    assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    portfolio = get(api, "/portfolio/virtual")["portfolio"]
    assert [(p["position"]["order_id"], p["unrealized_r"]) for p in portfolio["positions"]] == sorted(
        [(str(auto_id), "0.5"), (str(manual_id), "0.5")])
    assert portfolio["totals"]["unrealized_r"] == "1"

    for hm in ("11:30", "13:00"):
        api.clock.set(et(DAY, hm))
        assert jobs.live_cycle() == JobResult("live_cycle", True, "COMPLETED")
    api.clock.set(et(DAY, "16:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")

    for order_id, expected_bars in ((auto_id, 201), (manual_id, 170)):
        detail = get(api, f"/orders/{order_id}")
        assert [e["type"] for e in detail["events"]] == [
            "ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
        assert (detail["order"]["status"], detail["order"]["r_multiple"]) == ("CLOSED", "1.75")
        assert detail["data_quality"]["events"][0]["payload"]["expected_bars"] == expected_bars
        chart = get(api, f"/orders/{order_id}/chart")
        assert [m["type"] for m in chart["markers"]] == ["FILLED", "TARGET1_HIT", "TARGET2_HIT"]
        bars = get(api, "/market/bars", ticker=chart["ticker"], source=chart["price_source"],
                   **{"from": chart["window"]["from"], "to": chart["window"]["to"]})
        assert len(bars["bars"]) == len(bars["vwap"]) == 201

    summary = get(api, "/metrics")["groups"][0]["summary"]
    assert (summary["trades"], summary["win_rate"], summary["expectancy_r"], summary["execution_rate"]) == (
        2, 1.0, 1.75, 1.0)
    assert "INSUFFICIENT_SAMPLE" in summary["warnings"]
    by_origin = {g["key"]: g["summary"]["trades"] for g in get(api, "/metrics", group_by="origin")["groups"]}
    assert by_origin == {"AUTO_STRATEGY": 1, "MANUAL_USER": 1}
    assert get(api, "/portfolio/virtual")["portfolio"]["positions"] == []

    replay = post_json(api.client, "/replay", {"mode": "REPRODUCE", "from": et(DAY, "08:00").isoformat(),
                                               "to": et(DAY, "23:00").isoformat()})
    assert replay.status_code == 200, replay.text
    report = replay.json()
    assert report["failures"] == {} and set(report["created"]) == {str(auto_id), str(manual_id)}
    for source_id, replay_id in report["created"].items():
        assert identities(api, replay_id) == identities(api, source_id)
    replays = get(api, "/metrics", replay="true")["groups"][0]["summary"]
    assert (replays["trades"], replays["expectancy_r"], replays["win_rate"]) == (2, 1.75, 1.0)
