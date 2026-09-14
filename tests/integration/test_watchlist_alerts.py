from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from tests.integration.support import CODE_VERSION, DAY, PRICE_SOURCE, FakeBarSource, count, feeds, flat_raw, raw
from tests.support import et
from virtual_orders.alerts import watch as watch_module
from virtual_orders.alerts.watch import first_minute_at_or_after, run_watchlist_cycle
from virtual_orders.alerts.watchlist import add_ticker, create_rule, parse_rule_body
from virtual_orders.ledger.runs import RunStatus, latest_run_status
from virtual_orders.storage import tables

MSFT = "MSFT"
CREATED = et(DAY, "09:00")


def crossing_bars():
    return (
        flat_raw(DAY, "09:30", "10:00", 100, MSFT)
        + [raw(DAY, "10:00", 100, 101.6, 99.9, 101.5, ticker=MSFT)]
        + flat_raw(DAY, "10:01", "10:10", 101.5, MSFT)
        + [raw(DAY, "10:10", 101.5, 101.5, 100.4, 100.5, ticker=MSFT),
           raw(DAY, "10:11", 100.5, 101.3, 100.5, 101.2, ticker=MSFT)]
        + flat_raw(DAY, "10:12", "10:40", 101.2, MSFT)
    )


def add_rule(engine, body, created_at=CREATED):
    with engine.begin() as conn:
        add_ticker(conn, MSFT, added_at=created_at)
        return create_rule(conn, MSFT, parse_rule_body(body), created_at=created_at)


def cross(direction="ABOVE", cooldown=0):
    return {"kind": "PRICE_CROSS", "level": Decimal("101"), "direction": direction, "cooldown_minutes": cooldown}


def cycle(engine, source, hm, alerts_enabled=True):
    return run_watchlist_cycle(engine, feeds(source), price_source=PRICE_SOURCE, code_version=CODE_VERSION,
                               market_now=et(DAY, hm), alerts_enabled=alerts_enabled)


def key(kind, rule, hm):
    return f"{kind}:{rule.id}:{et(DAY, hm).isoformat()}"


def documents(engine):
    with engine.connect() as conn:
        return {row.alert_key: row.document for row in conn.execute(select(tables.alert_outbox))}


def test_first_minute_at_or_after():
    assert first_minute_at_or_after(et(DAY, "10:10")) == et(DAY, "10:10")
    assert first_minute_at_or_after(et(DAY, "10:10", 30)) == et(DAY, "10:11")


def test_price_crossings_are_enqueued_once_with_cooldown(engine):
    fast, slow, down = add_rule(engine, cross()), add_rule(engine, cross(cooldown=30)), add_rule(engine, cross("BELOW"))
    source = FakeBarSource(crossing_bars())

    report = cycle(engine, source, "10:30")

    assert set(report.enqueued) == {
        key("PRICE_CROSS", fast, "10:00"), key("PRICE_CROSS", fast, "10:11"),
        key("PRICE_CROSS", slow, "10:00"), key("PRICE_CROSS", down, "10:10"),
    }
    document = documents(engine)[key("PRICE_CROSS", fast, "10:00")]
    assert document["kind"] == "PRICE_CROSS" and document["ticker"] == MSFT and document["direction"] == "ABOVE"
    assert Decimal(document["level"]) == Decimal("101") and Decimal(document["bar"]["close"]) == Decimal("101.5")
    assert document["price_source"] == PRICE_SOURCE and document["run_id"] == str(report.run_id)

    assert cycle(engine, source, "10:32").enqueued == ()
    assert count(engine, "alert_outbox") == 4


def test_a_rule_never_alerts_on_bars_before_its_creation_but_uses_the_previous_close(engine):
    late = add_rule(engine, cross(), created_at=et(DAY, "10:10", 30))
    assert cycle(engine, FakeBarSource(crossing_bars()), "10:30").enqueued == (key("PRICE_CROSS", late, "10:11"),)


def test_ingestion_resumes_from_the_last_stored_bar_and_failures_are_recorded(engine):
    add_rule(engine, cross())
    source = FakeBarSource(crossing_bars())
    cycle(engine, source, "10:00")
    cycle(engine, source, "10:05")
    assert source.calls == [(MSFT, et(DAY, "09:30"), et(DAY, "10:00")), (MSFT, et(DAY, "10:00"), et(DAY, "10:05"))]

    source.failing.add(MSFT)
    report = cycle(engine, source, "10:30")

    assert report.ingest_failures == {f"{PRICE_SOURCE}:{MSFT}": "SourceUnavailable"} and report.enqueued == ()
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status is RunStatus.COMPLETED and detail["ingest_failures"] == report.ingest_failures


def test_outside_a_session_or_without_tickers_nothing_runs(engine):
    source = FakeBarSource(crossing_bars())
    assert cycle(engine, source, "10:30").run_id is None  # empty watchlist
    add_rule(engine, cross())
    assert cycle(engine, source, "08:00").run_id is None and source.calls == []
    assert count(engine, "evaluation_runs") == 0


def test_alerts_disabled_still_ingests_but_enqueues_nothing(engine):
    add_rule(engine, cross())
    source = FakeBarSource(crossing_bars())
    report = cycle(engine, source, "10:30", alerts_enabled=False)
    assert report.run_id is not None and report.enqueued == () and count(engine, "alert_outbox") == 0
    assert source.calls and count(engine, "bars_1m") > 0


def test_pressure_rule_is_labelled_as_an_estimate(engine):
    rule = add_rule(engine, {"kind": "PRESSURE", "cmf_threshold": Decimal("0.5"), "window_bars": 5})
    bars = [raw(DAY, f"09:3{i}", 100 + i, 100.5 + i, 99.8 + i, 100.5 + i, ticker=MSFT) for i in range(5)]
    source = FakeBarSource(bars)

    assert cycle(engine, source, "09:34").enqueued == ()  # four closed bars: window not full
    report = cycle(engine, source, "09:40")

    assert report.enqueued == (key("PRESSURE", rule, "09:34"),)
    document = documents(engine)[report.enqueued[0]]
    assert document["estimate"] is True and document["side"] == "BUY"
    assert document["method"] == "OHLCV_PRESSURE_ESTIMATE_V1" and "not order-flow" in document["disclaimer"]
    assert Decimal(document["values"]["chaikin_money_flow"]) == Decimal("1")
    assert datetime.fromisoformat(document["values"]["last_bar_ts"]) == et(DAY, "09:34")


def test_one_failing_rule_never_stops_the_others(engine, monkeypatch):
    broken = add_rule(engine, cross())
    working = add_rule(engine, cross("BELOW"))
    original = watch_module.price_crossings

    def exploding(bars, *, level, direction, cooldown, last_alert_ts=None):
        if direction == "ABOVE":
            raise RuntimeError("boom")
        return original(bars, level=level, direction=direction, cooldown=cooldown, last_alert_ts=last_alert_ts)

    monkeypatch.setattr(watch_module, "price_crossings", exploding)
    report = cycle(engine, FakeBarSource(crossing_bars()), "10:30")

    assert report.rule_errors == {str(broken.id): "ERROR:RuntimeError"}
    assert report.enqueued == (key("PRICE_CROSS", working, "10:10"),)


def test_an_unexpected_ingest_error_is_isolated_to_its_ticker(engine):
    class ExplodingForAapl(FakeBarSource):
        def fetch_bars(self, ticker, start, end):
            if ticker == "AAPL":
                raise RuntimeError("provider-secret-text")
            return super().fetch_bars(ticker, start, end)

    fast = add_rule(engine, cross())  # MSFT, sorted after AAPL: it must still be ingested and evaluated
    with engine.begin() as conn:
        add_ticker(conn, "AAPL", added_at=CREATED)
    source = ExplodingForAapl(crossing_bars())

    report = cycle(engine, source, "10:30")

    assert report.ingest_failures == {f"{PRICE_SOURCE}:AAPL": "ERROR:RuntimeError"}
    assert key("PRICE_CROSS", fast, "10:00") in report.enqueued
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status is RunStatus.COMPLETED and "provider-secret-text" not in str(detail)
