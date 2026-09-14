from uuid import UUID, uuid4

from tests.integration.api.conftest import post_json
from tests.integration.support import CODE_VERSION, DAY, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders


def evaluated_order(api, hms=("10:30", "11:30", "13:00"), **overrides) -> UUID:
    order_id = UUID(post_json(api.client, "/signals", signal_body(**overrides)).json()["auto_order_id"])
    api.bars.load(scenario_bars(ticker=overrides.get("ticker", "AAPL")))
    for hm in hms:
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def test_list_orders_projects_state_and_filters(api):
    closed = evaluated_order(api)
    pending = UUID(post_json(api.client, "/signals", signal_body(client_signal_id="p", strategy="OTHER",
                                                                 ticker="MSFT")).json()["auto_order_id"])
    flag_order_review(api.services.engine, pending, reason="MANUAL", ref="look")
    reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[closed])

    everything = api.client.get("/orders").json()["orders"]
    assert {o["order_id"] for o in everything} == {str(closed), str(pending)}  # replay=false by default
    by_id = {o["order_id"]: o for o in everything}
    assert by_id[str(closed)]["status"] == "CLOSED" and by_id[str(closed)]["r_multiple"] == "1.75"
    assert by_id[str(closed)]["strategy"] == "REXSHARE" and by_id[str(closed)]["origin"] == "AUTO_STRATEGY"

    def ids(**params):
        return [o["order_id"] for o in api.client.get("/orders", params=params).json()["orders"]]

    assert ids(status="CLOSED") == [str(closed)]
    assert ids(strategy="OTHER") == [str(pending)]
    assert ids(needs_review="true") == [str(pending)]
    assert ids(origin="MANUAL_USER") == []
    replays = api.client.get("/orders", params={"replay": "true"}).json()["orders"]
    assert len(replays) == 1 and replays[0]["replay_of_order_id"] == str(closed)
    invalid_status = api.client.get("/orders", params={"status": "NOPE"})
    assert invalid_status.status_code == 422 and invalid_status.json()["error"]["code"] == "REQUEST_INVALID"


def test_order_detail_has_events_segments_quality_and_bars(api):
    order_id = evaluated_order(api)
    response = api.client.get(f"/orders/{order_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["order"]["order_id"] == str(order_id) and detail["order"]["price_source"] == "fake_feed"
    assert detail["state"]["status"] == "CLOSED"
    keys = [e["event_key"] for e in detail["events"]]
    assert keys[0] == "ORDER_CREATED" and "FILLED" in keys and "TARGET2_HIT" in keys
    assert [e["seq"] for e in detail["events"]] == list(range(1, len(detail["events"]) + 1))
    assert len(detail["segments"]) == 3 and all(s["selected_data_hash"] for s in detail["segments"])
    assert detail["data_quality"] == {"expected_bars": 0, "missing_bars": 0, "events": [], "rechecks": []}
    assert detail["bars"][0]["ts"] == et(DAY, "09:30").isoformat()
    assert detail["bars"][-1]["ts"] == et(DAY, "12:50").isoformat()
    assert detail["bars"][0]["open"] == "105"


def test_order_detail_without_segments_has_no_bars_and_unknown_is_404(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    detail = api.client.get(f"/orders/{order_id}").json()
    assert detail["segments"] == [] and detail["bars"] == [] and detail["state"]["status"] == "PENDING"
    missing = api.client.get(f"/orders/{uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ORDER_NOT_FOUND"
