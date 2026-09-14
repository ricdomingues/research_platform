from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from tests.integration.support import CODE_VERSION, DAY, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.storage import tables

MARKET_ERROR = "MARKET_REQUEST_INVALID"


def closed_order(api) -> UUID:
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    api.bars.load(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def get(api, path, **params):
    response = api.client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_bars_are_stored_bars_as_of_now_with_a_session_anchored_vwap_and_no_provider_call(api):
    closed_order(api)
    calls = len(api.bars.calls)

    body = get(api, "/market/bars", ticker=" aapl ", **{"from": et(DAY, "10:00").isoformat(),
                                                        "to": et(DAY, "10:10").isoformat()})

    assert len(api.bars.calls) == calls  # stored bars only (D41)
    assert (body["ticker"], body["price_source"], body["vwap_method"]) == ("AAPL", "fake_feed",
                                                                          "SESSION_VWAP_TYPICAL_PRICE_V1")
    assert datetime.fromisoformat(body["data_as_of"]).tzinfo is not None
    assert [b["ts"] for b in body["bars"]] == [et(DAY, f"10:0{m}").isoformat() for m in range(10)]
    assert (body["bars"][5]["open"], body["bars"][5]["volume"]) == ("101", "1000")
    assert body["vwap"][0] == {"ts": et(DAY, "10:00").isoformat(), "value": "105"}  # anchored at 09:30, not 10:00
    assert body["vwap"][5] == {"ts": et(DAY, "10:05").isoformat(), "value": "104.8907"}


def test_bar_windows_are_validated(api):
    start = et(DAY, "10:00").isoformat()
    cases = [
        ({"ticker": "AAPL", "from": start, "to": et(DAY, "09:00").isoformat()}, ["EMPTY_WINDOW"]),
        ({"ticker": "AAPL", "from": start, "to": et("2025-12-03", "10:00").isoformat()}, ["WINDOW_TOO_LARGE"]),
        ({"ticker": "AAPL", "from": "2025-11-25T10:00:00", "to": "2025-11-25T11:00:00"},
         ["NAIVE_DATETIME:from", "NAIVE_DATETIME:to"]),
    ]
    for params, errors in cases:
        response = api.client.get("/market/bars", params=params)
        assert response.status_code == 422, params
        assert response.json() == {"error": {"code": MARKET_ERROR, "reason": None, "detail": {"errors": errors}}}
    blank = api.client.get("/market/bars", params={"ticker": "  ", "from": start, "to": et(DAY, "11:00").isoformat()})
    assert blank.status_code == 422 and blank.json()["error"]["code"] == "TICKER_INVALID"
    missing = api.client.get("/market/bars", params={"ticker": "AAPL"})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "REQUEST_INVALID"


def test_pressure_is_always_labelled_as_an_estimate(api):
    closed_order(api)  # bars up to 12:50: the last 30 are 12:21-12:49 flat at 107 and the 12:50 bar closing 110

    plain = get(api, "/market/pressure", ticker="AAPL")
    assert (plain["estimate"], plain["method"], plain["available"], plain["reason"]) == (
        True, "OHLCV_PRESSURE_ESTIMATE_V1", True, None)
    assert plain["disclaimer"] == "Estimate derived from 1-minute OHLCV bars; it is not order-flow or trade-side data."
    values = plain["values"]
    assert (values["bars"], values["last_bar_ts"]) == (30, et(DAY, "12:50").isoformat())
    assert (values["chaikin_money_flow"], values["obv_slope"]) == ("0.0137", "0.0345")
    assert plain["side"] is None and plain["cmf_threshold"] is None

    assert get(api, "/market/pressure", ticker="AAPL", cmf_threshold="0.01")["side"] == "BUY"
    assert get(api, "/market/pressure", ticker="AAPL", cmf_threshold="0.3")["side"] is None

    short = get(api, "/market/pressure", ticker="AAPL", window_bars=300)
    assert (short["available"], short["reason"], short["values"], short["estimate"]) == (
        False, "INSUFFICIENT_BARS", None, True)
    nothing = get(api, "/market/pressure", ticker="MSFT")
    assert (nothing["available"], nothing["reason"], nothing["method"]) == (False, "NO_BARS",
                                                                           "OHLCV_PRESSURE_ESTIMATE_V1")


def test_pressure_parameters_are_validated(api):
    for params, errors in (
        ({"window_bars": 4}, ["OUT_OF_RANGE:window_bars"]),
        ({"cmf_threshold": "1"}, ["OUT_OF_RANGE:cmf_threshold"]),
        ({"window_bars": 391, "cmf_threshold": "0"}, ["OUT_OF_RANGE:window_bars", "OUT_OF_RANGE:cmf_threshold"]),
    ):
        response = api.client.get("/market/pressure", params={"ticker": "AAPL", **params})
        assert response.status_code == 422, params
        assert response.json()["error"] == {"code": MARKET_ERROR, "reason": None, "detail": {"errors": errors}}


def test_order_chart_has_levels_markers_fill_model_and_a_bar_window(api):
    order_id = closed_order(api)

    chart = get(api, f"/orders/{order_id}/chart")
    detail = get(api, f"/orders/{order_id}")

    assert (chart["ticker"], chart["direction"], chart["status"], chart["price_source"]) == (
        "AAPL", "LONG", "CLOSED", "fake_feed")
    levels = chart["levels"]
    assert {name: levels[name] for name in ("entry_zone_low", "entry_zone_high", "stop", "target1", "target2",
                                            "trigger_price")} == {
        "entry_zone_low": "100", "entry_zone_high": "102", "stop": "97", "target1": "106", "target2": "110",
        "trigger_price": None,
    }
    assert (levels["avg_entry"], levels["stop_current"]) == (detail["order"]["avg_entry"],
                                                            detail["order"]["stop_current"])
    assert [(m["type"], m["ts"], m["price"]) for m in chart["markers"]] == [
        ("FILLED", et(DAY, "10:05").isoformat(), "101"),
        ("TARGET1_HIT", et(DAY, "11:00").isoformat(), "106"),
        ("TARGET2_HIT", et(DAY, "12:50").isoformat(), "110"),
    ]
    with api.services.engine.connect() as conn:
        snapshot = conn.execute(select(tables.orders.c.config_snapshot)
                                .where(tables.orders.c.id == order_id)).scalar_one()
    assert chart["fill_model_version"] == DEFAULT_FILL_MODEL_VERSION and chart["config_snapshot"] == snapshot
    final = datetime.fromisoformat(detail["state"]["final_event_ts"])
    assert chart["window"] == {"from": et(DAY, "00:00").isoformat(),
                               "to": (final + timedelta(minutes=1)).isoformat()}
    assert chart["evaluation_start_ts"] == et(DAY, "09:30").isoformat()
    assert chart["window_truncated"] is False  # closed the same day: far inside the 7-day cap (M10)

    missing = api.client.get(f"/orders/{uuid4()}/chart")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ORDER_NOT_FOUND"
