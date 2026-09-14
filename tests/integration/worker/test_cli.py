import io
import json

from sqlalchemy import text

from tests.config_support import BASE_ENV
from tests.integration.support import submit_default
from virtual_orders.worker.__main__ import main


def test_rebuild_projections_prints_a_summary_and_signals_problems_by_exit_code(worker):
    engine = worker.services.engine
    order_id = submit_default(engine).auto_order_id

    out = io.StringIO()
    assert main(["rebuild-projections"], environ=BASE_ENV, services_factory=lambda settings: worker.services,
                out=out) == 0
    assert json.loads(out.getvalue()) == {"orders": 1, "problems": {}, "results": {"OK": 1}}
    assert worker.closed == [1]

    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET qty_open = 9 WHERE order_id = :id"), {"id": order_id})
    out = io.StringIO()
    assert main(["rebuild-projections", "--order-id", str(order_id)], environ=BASE_ENV,
                services_factory=lambda settings: worker.services, out=out) == 1
    assert json.loads(out.getvalue())["problems"] == {str(order_id): "PROJECTION_INTEGRITY_ERROR"}


def test_run_is_the_default_command(worker):
    calls = []
    code = main([], environ=BASE_ENV, services_factory=lambda settings: worker.services,
                worker=lambda services: calls.append(services) or 0)
    assert code == 0 and calls == [worker.services]


def test_invalid_configuration_exits_2_naming_variables_only():
    err = io.StringIO()
    assert main(["run"], environ={"API_KEY": "secret-value"}, err=err) == 2
    payload = json.loads(err.getvalue())
    assert payload["error"] == "CONFIG_INVALID" and "MISSING:DATABASE_URL" in payload["errors"]
    assert "secret-value" not in err.getvalue()


def test_run_rejects_an_interval_the_cron_cannot_express_before_building_services():
    err = io.StringIO()
    built = []
    code = main(["run"], environ={**BASE_ENV, "EVAL_INTERVAL_MINUTES": "60"},
                services_factory=lambda settings: built.append(settings), err=err)
    assert code == 2 and built == []
    assert json.loads(err.getvalue()) == {"error": "CONFIG_INVALID", "errors": ["OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"]}
