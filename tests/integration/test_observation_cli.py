import io
import json

import pytest
from sqlalchemy.exc import InterfaceError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from tests.integration.support import CODE_VERSION, DAY, FakeBarSource, count, feeds, scenario_bars, submit_default
from tests.support import et
from virtual_orders.analytics.observation import ASSOCIATION_NOTE
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.observation import __main__ as cli

HISTORY = ("evaluation_runs", "order_events", "bar_batches", "alert_outbox", "worker_sessions")


def closed_trade(engine):
    source = FakeBarSource(scenario_bars())
    submit_default(engine)
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def run(argv, environ):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, environ=environ, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_report_prints_markdown_or_json_and_never_writes(engine, database_url):
    closed_trade(engine)
    environ = {"DATABASE_URL": database_url, "API_KEY": "never-read"}
    before = {name: count(engine, name) for name in HISTORY}

    code, markdown, err = run(["report", "--day", DAY], environ)
    assert (code, err) == (0, "")
    assert markdown.startswith("# Observação em paper — pregão 2025-11-25\n")
    for heading in ("## Falhas de provider", "## Candles ausentes", "## Actionability (503)", "## DATA_QUALITY_RECHECK",
                    "## Entrega de alertas", "## Reinícios do worker", "## Transições de saúde", "## Trades virtuais",
                    "## Latência sinal → fill", "## Pressão estimada × resultado", "## Definições"):
        assert heading in markdown, heading
    assert METHOD in markdown and DISCLAIMER in markdown and ASSOCIATION_NOTE in markdown
    assert "vo:vo@" not in markdown and "never-read" not in markdown

    code, text, err = run(["report", "--day", DAY, "--format", "json"], environ)
    document = json.loads(text)
    assert (code, err, document["session_day"]) == (0, "", DAY)
    assert (document["trades"]["stats"]["trades"], document["trades"]["stats"]["sum_r"]) == (1, "1.75")
    assert document["pressure"]["estimate"] is True
    assert {name: count(engine, name) for name in HISTORY} == before


def test_summary_prints_one_row_per_session(engine, database_url):
    closed_trade(engine)
    code, markdown, _ = run(["summary", "--from", DAY, "--to", "2025-11-26"], {"DATABASE_URL": database_url})
    assert code == 0
    assert markdown.startswith("# Observação em paper — resumo 2025-11-25 a 2025-11-26\n")
    assert "| 2025-11-25 |" in markdown and "| 2025-11-26 |" in markdown


def test_exit_codes_carry_codes_and_types_only(engine, database_url, monkeypatch):
    code, out, err = run(["report", "--day", "2025-11-29"], {"DATABASE_URL": database_url})
    assert (code, out) == (3, "")
    assert json.loads(err) == {"error": "OBSERVATION_REQUEST_INVALID", "errors": ["NOT_A_SESSION:2025-11-29"]}

    code, out, err = run(["report", "--day", DAY], {})
    assert (code, json.loads(err)) == (2, {"error": "CONFIG_INVALID", "errors": ["MISSING:DATABASE_URL"]})

    code, out, err = run(["report", "--day", DAY], {"DATABASE_URL": "postgresql+psycopg://vo:hunter2@127.0.0.1:1/x"})
    assert (code, out, json.loads(err)) == (4, "", {"error": "DATABASE_UNAVAILABLE", "type": "OperationalError"})
    assert "hunter2" not in err

    def exploding(*args, **kwargs):
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr(cli, "build_observation_report", exploding)
    code, out, err = run(["report", "--day", DAY], {"DATABASE_URL": database_url})
    assert (code, json.loads(err)) == (1, {"error": "INTERNAL_ERROR", "type": "RuntimeError"})
    assert "hunter2" not in err

    with pytest.raises(SystemExit) as usage:
        cli.main(["report", "--day", "25/11/2025"], environ={"DATABASE_URL": database_url}, err=io.StringIO())
    assert usage.value.code == 2


def raising(error):
    def build(*args, **kwargs):
        raise error

    return build


@pytest.mark.parametrize("error, exit_code, payload", [
    (InterfaceError("SELECT 1", {}, Exception("password=hunter2")), 4,
     {"error": "DATABASE_UNAVAILABLE", "type": "InterfaceError"}),
    (PoolTimeoutError("QueuePool limit reached; password=hunter2"), 4,
     {"error": "DATABASE_UNAVAILABLE", "type": "TimeoutError"}),
    (ProgrammingError("SELECT 1", {}, Exception("password=hunter2")), 1,
     {"error": "DATABASE_ERROR", "type": "ProgrammingError"}),
], ids=["interface-error", "pool-timeout", "programming-error"])
def test_only_connection_errors_are_database_unavailable(database_url, monkeypatch, error, exit_code, payload):
    # D70b: a SQL defect (ProgrammingError, DataError, ...) is never reported as an unreachable database.
    monkeypatch.setattr(cli, "build_observation_report", raising(error))
    code, out, err = run(["report", "--day", DAY], {"DATABASE_URL": database_url})
    assert (code, out, json.loads(err)) == (exit_code, "", payload)
    assert "hunter2" not in err
