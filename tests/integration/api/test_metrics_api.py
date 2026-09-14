from uuid import UUID

from tests.integration.api.conftest import post_json
from tests.integration.support import CODE_VERSION, DAY, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders


def cycle(api, hm):
    run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))


def two_closed_orders(api) -> tuple[UUID, UUID]:
    api.bars.load(scenario_bars())
    first = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    second = UUID(post_json(api.client, "/signals", signal_body(client_signal_id="second",
                                                                strategy="OTHER")).json()["auto_order_id"])
    cycle(api, "10:30")
    flag_order_review(api.services.engine, second, reason="MANUAL", ref="looked-odd")
    cycle(api, "11:30")
    cycle(api, "13:00")
    return first, second


def metrics(api, **params):
    response = api.client.get("/metrics", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_needs_review_is_excluded_by_default_and_reported(api):
    two_closed_orders(api)
    default = metrics(api)
    assert [g["key"] for g in default["groups"]] == [None]
    summary = default["groups"][0]["summary"]
    assert summary["trades"] == 1 and summary["avg_r"] == 1.75
    assert summary["excluded_needs_review"] == {"count": 1, "reasons": {"MANUAL": 1}}
    assert "INSUFFICIENT_SAMPLE" in summary["warnings"]

    included = metrics(api, include_needs_review="true")["groups"][0]["summary"]
    assert included["trades"] == 2 and included["included_needs_review"]["count"] == 1


def test_group_by_strategy_and_determinism(api):
    two_closed_orders(api)
    grouped = metrics(api, group_by="strategy", include_needs_review="true")
    assert grouped["group_by"] == "strategy"
    assert [(g["key"], g["summary"]["trades"]) for g in grouped["groups"]] == [("OTHER", 1), ("REXSHARE", 1)]
    assert metrics(api, group_by="strategy", include_needs_review="true") == grouped


def test_replay_and_interval_filters(api):
    first, _ = two_closed_orders(api)
    reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[first])
    replays = metrics(api, replay="true")
    assert replays["groups"][0]["summary"]["trades"] == 1
    interval = {"from": "2025-11-26T00:00:00Z", "to": "2025-11-27T00:00:00Z"}
    later = metrics(api, **interval)
    assert [g["key"] for g in later["groups"]] == [None]
    empty = later["groups"][0]["summary"]
    assert empty["trades"] == 0 and empty["excluded_needs_review"] == {"count": 0, "reasons": {}}
    assert "INSUFFICIENT_SAMPLE" in empty["warnings"]
    assert metrics(api, group_by="strategy", **interval)["groups"] == []


def test_invalid_metric_parameters_are_422(api):
    for params in ({"group_by": "ticker"}, {"from": "2025-11-26T00:00:00"}):
        response = api.client.get("/metrics", params=params)
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "REQUEST_INVALID"
