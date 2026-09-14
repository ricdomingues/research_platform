from decimal import Decimal

import pytest

from core.domain.models import FillConfig
from tests.integration.support import CODE_VERSION, PRICE_SOURCE, SIGNAL_CREATED_AT, count, signal_body, submit_default
from virtual_orders.evaluator import replay
from virtual_orders.evaluator.replay import ReplaySelectionError, recalculate_orders
from virtual_orders.evaluator.signals import submit_signal


def strict_order(engine):
    return submit_signal(
        engine, signal_body(client_signal_id="strict"), config=FillConfig(stop_slippage_bps=Decimal("7")),
        code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=SIGNAL_CREATED_AT,
    ).auto_order_id


def install_cross_field_rule(monkeypatch):
    original = replay.config_from_snapshot

    def cross_field_rule(document):
        config = original(document)
        if config.stop_slippage_bps == Decimal("7") and config.risk_amount == Decimal("250"):
            raise ValueError("risk_amount 250 is not allowed with stop_slippage_bps 7 (test rule)")
        return config

    monkeypatch.setattr(replay, "config_from_snapshot", cross_field_rule)


def test_overrides_invalid_for_one_source_configuration_fail_before_the_run(engine, monkeypatch):
    lenient = submit_default(engine).auto_order_id
    strict = strict_order(engine)
    install_cross_field_rule(monkeypatch)
    runs_before = count(engine, "evaluation_runs")

    with pytest.raises(ReplaySelectionError, match=str(strict)) as caught:
        recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[lenient, strict],
                           config_overrides={"risk_amount": "250"})

    assert str(lenient) not in str(caught.value)
    assert count(engine, "evaluation_runs") == runs_before
    assert count(engine, "orders") == 2


def test_overrides_valid_for_every_source_still_open_the_run(engine, monkeypatch):
    lenient = submit_default(engine).auto_order_id
    strict = strict_order(engine)
    install_cross_field_rule(monkeypatch)

    report = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[lenient, strict],
                                config_overrides={"risk_amount": "200"})

    assert report.failures == {} and set(report.created) == {lenient, strict}
