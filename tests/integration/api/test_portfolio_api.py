from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from tests.integration.support import API_KEY, CODE_VERSION, DAY, post_json, scenario_bars, signal_body
from tests.support import et
from virtual_orders.api.app import create_app
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.portfolio.sources import RealPosition


def cycle(api, hm):
    run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))


def test_virtual_portfolio_marks_open_positions_at_the_last_stored_close_and_empties_when_closed(api):
    post_json(api.client, "/signals", signal_body())
    post_json(api.client, "/signals", signal_body(client_signal_id="msft", ticker="MSFT"))
    api.bars.load(scenario_bars() + scenario_bars(ticker="MSFT"))
    cycle(api, "10:30")  # both filled at 101 on the 10:05 bar; the last stored close is the 10:29 bar at 103

    body = api.client.get("/portfolio/virtual").json()

    assert body["kind"] == "VIRTUAL" and body["portfolio"]["basis"] == "PER_1R_NORMALIZED"
    assert "not account money" in body["portfolio"]["disclaimer"]
    rows = [(p["position"]["ticker"], p["position"]["qty_open"], p["position"]["last_close"], p["unrealized_pnl"],
             p["unrealized_r"], p["open_r"], p["allocation_pct"]) for p in body["portfolio"]["positions"]]
    assert rows == [("AAPL", "25", "103", "50", "0.5", "0.5", "50"), ("MSFT", "25", "103", "50", "0.5", "0.5", "50")]
    assert body["portfolio"]["totals"] == {"positions": 2, "marked": 2, "unmarked": 0, "notional": "5150",
                                           "unrealized_pnl": "100", "unrealized_r": "1", "risk_amount": "200"}

    cycle(api, "11:30")
    cycle(api, "13:00")
    assert api.client.get("/portfolio/virtual").json()["portfolio"]["positions"] == []


def test_real_portfolio_is_unavailable_until_phase_0(api):
    assert api.services.portfolio_source is None
    assert api.client.get("/portfolio/real").json() == {
        "kind": "REAL", "available": False, "reason": "PHASE_0_PENDING", "source": None, "positions": [],
    }


def test_a_failing_real_source_reports_only_its_type(api):
    class FailingBroker:
        name = "fake_broker"

        def list_positions(self):
            raise RuntimeError("token=secret-value")

    services = replace(api.services, portfolio_source=FailingBroker())
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}) as client:
        response = client.get("/portfolio/real")
    assert response.json() == {"kind": "REAL", "available": False, "reason": "SOURCE_UNAVAILABLE",
                               "source": "fake_broker", "error": "RuntimeError", "positions": []}
    assert "secret-value" not in response.text


def test_a_working_real_source_reports_its_positions_and_never_mixes_totals_with_virtual(api):
    post_json(api.client, "/signals", signal_body())
    api.bars.load(scenario_bars())
    cycle(api, "10:30")  # AAPL filled and open: the virtual portfolio now carries its own totals

    class WorkingBroker:
        name = "fake_broker"

        def list_positions(self):
            return [RealPosition(ticker="MSFT", quantity=Decimal("10"), average_cost=Decimal("150.25"),
                                 market_value=Decimal("1602.50"), as_of=datetime(2025, 11, 25, 15, 30, tzinfo=UTC))]

    services = replace(api.services, portfolio_source=WorkingBroker())
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}) as client:
        real = client.get("/portfolio/real").json()
        virtual = client.get("/portfolio/virtual").json()

    # Exact keys and Decimal/datetime serialization that the dashboard's real_portfolio_rows relies on (D46, T8).
    assert real == {"kind": "REAL", "available": True, "reason": None, "source": "fake_broker", "positions": [
        {"ticker": "MSFT", "quantity": "10", "average_cost": "150.25", "market_value": "1602.5",
         "as_of": "2025-11-25T15:30:00+00:00"},
    ]}
    assert "totals" not in real  # the real slot never carries a total of its own, let alone a combined one
    assert virtual["portfolio"]["totals"]["positions"] == 1  # the virtual side is unaffected by the real source
