import logging
import threading
from dataclasses import replace

import httpx
import pytest

from tests.config_support import BASE_ENV
from virtual_orders.bootstrap import build_services
from virtual_orders.config import load_settings
from virtual_orders.worker.runner import EXIT_DATABASE_UNAVAILABLE, ShutdownGuard, run_worker


def test_shutdown_guard_is_claimed_exactly_once_across_threads():
    guard = ShutdownGuard()
    barrier = threading.Barrier(16)
    results: list[bool] = []

    def contend() -> None:
        barrier.wait()
        results.append(guard.claim())

    threads = [threading.Thread(target=contend) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert guard.claim() is False  # already claimed: every later claim loses


def test_database_down_at_start_logs_only_the_type_and_exits_4(caplog):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    services = build_services(load_settings(BASE_ENV), http_client=client)  # DATABASE_URL points at 127.0.0.1:1
    closed: list[int] = []
    original_close = services.close

    def close() -> None:
        closed.append(1)
        original_close()

    services = replace(services, close=close)

    def never_scheduled():
        pytest.fail("the scheduler must not be built without the worker lock")

    with caplog.at_level(logging.ERROR, logger="virtual_orders.worker"):
        code = run_worker(services, scheduler_factory=never_scheduled, install_signal_handlers=False)

    assert code == EXIT_DATABASE_UNAVAILABLE == 4
    assert closed == [1]
    assert "database unavailable at worker start: OperationalError" in caplog.text
    assert "127.0.0.1" not in caplog.text and "unused" not in caplog.text
    client.close()
