from datetime import date
from uuid import UUID

from tests.integration.api.conftest import post_json
from tests.integration.support import CODE_VERSION, DAY, FakeReference, scenario_bars, signal_body
from tests.support import et
from virtual_orders.evaluator.quality import run_end_of_day


def test_order_detail_shows_real_data_quality_and_data_gap_events(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    api.bars.load([b for b in scenario_bars() if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")])
    run_end_of_day(api.services.engine, gateway=api.services.gateway, reference=FakeReference(),
                   session_day=date(2025, 11, 25), code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    quality = api.client.get(f"/orders/{order_id}").json()["data_quality"]

    assert (quality["expected_bars"], quality["missing_bars"]) == (201, 35)
    assert [e["type"] for e in quality["events"]] == ["DATA_QUALITY", "DATA_GAP"]
    assert quality["events"][0]["event_key"] == "DATA_QUALITY:2025-11-25"
    assert quality["events"][0]["payload"]["coverage_pct"] == "82.59"
    assert quality["events"][1]["payload"]["minutes"] == 35
    assert quality["rechecks"] == []
