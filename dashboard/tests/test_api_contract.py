"""D57: the client's envelope keys, the view-models and the figures against responses recorded from the real API
(tests/integration/api/test_dashboard_contract.py in the engine project). No server, no network."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from dashboard.charts import candlestick_figure, cumulative_r_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    REAL_PHASE_0_MESSAGE,
    comparison_rows,
    coverage_rows,
    cumulative_r,
    event_log_rows,
    gap_rows,
    health_log_rows,
    health_view,
    market_tickers,
    metric_cards,
    order_rows,
    outbox_rows,
    pressure_display,
    quality_view,
    real_portfolio_message,
    replay_pairs,
    review_exclusion_text,
    rule_rows,
    signal_rows,
    summary_warnings,
    virtual_portfolio_view,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "api"


def raw(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text(encoding="utf-8")


def load(name: str) -> Any:
    return json.loads(raw(name), parse_float=Decimal)


def serving(name: str) -> ApiClient:
    body = raw(name)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    return ApiClient("http://api.test", "key", transport=transport)


def test_the_client_unwraps_every_recorded_list_envelope():
    assert sorted(order["ticker"] for order in serving("orders").orders()) == ["AAPL", "AAPL", "MSFT"]
    assert [signal["ticker"] for signal in serving("signals").signals()] == ["AAPL"]
    assert [entry["ticker"] for entry in serving("watchlist").watchlist()] == ["MSFT"]
    assert "DEGRADED" in [entry["state"] for entry in serving("health_log").health_log()]
    assert [alert["alert_key"] for alert in serving("alert_outbox").alert_outbox()] == ["contract-alert"]


def test_overview_cards_curve_and_comparison_from_recorded_metrics():
    summary = load("metrics")["groups"][0]["summary"]
    cards = {card.label: card.value for card in metric_cards(summary)}
    assert (cards["Trades"], cards["Win rate"], cards["Expectância (R)"]) == ("2", "100.0%", "+1.75R")
    assert summary_warnings(summary) == ["Amostra insuficiente (menos de 30 trades)."]
    assert review_exclusion_text(summary).startswith("Excluídas por revisão: ")
    by_origin = {row["Grupo"]: row["Trades"] for row in comparison_rows(load("metrics_by_origin")["groups"])}
    assert by_origin == {"AUTO_STRATEGY": "1", "MANUAL_USER": "1"}
    points = cumulative_r(load("orders_closed")["orders"], include_needs_review=False)
    assert [point.cumulative_r for point in points] == [Decimal("1.75"), Decimal("3.50")]
    assert list(cumulative_r_figure(points).data[0].y) == [1.75, 3.5]


def test_order_tables_detail_quality_and_chart_from_recorded_orders():
    assert sorted(row["Ticker"] for row in order_rows(load("orders")["orders"])) == ["AAPL", "AAPL", "MSFT"]
    (signal,) = signal_rows(load("signals")["signals"])
    assert (signal["Ticker"], signal["Zona"], signal["Alvos"]) == ("AAPL", "100–102", "106 / 110")
    detail = load("order_detail")
    quality = quality_view(detail)
    assert (quality.expected_bars, quality.missing_bars, quality.coverage) == (201, 0, "100.00%")
    assert [row["Tipo"] for row in event_log_rows(detail["events"])] == [
        "ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"]
    chart, bars = load("order_chart"), load("market_bars")
    figure = candlestick_figure(bars["bars"], bars["vwap"], title=chart["ticker"], levels=chart["levels"],
                                markers=chart["markers"], evaluation_start_ts=chart["evaluation_start_ts"])
    assert len(figure.data[0].open) == 201
    assert [trace.name for trace in figure.data if getattr(trace, "mode", None) == "markers"] == [
        "FILLED", "TARGET1_HIT", "TARGET2_HIT"]
    assert {"Zona de entrada", "Stop", "Alvo 1", "Alvo 2"} <= {shape.name for shape in figure.layout.shapes}
    assert chart["window_truncated"] is False


def test_market_portfolio_watchlist_comparison_and_health_from_recorded_responses():
    display = pressure_display(load("pressure"))
    assert display.method == "OHLCV_PRESSURE_ESTIMATE_V1" and "CMF: 0.0137" in display.lines
    assert display.lines[-1] == "Pressão forte estimada: BUY (limiar CMF 0.01)"
    portfolio = virtual_portfolio_view(load("portfolio_virtual"))
    assert [row["R não realizado"] for row in portfolio.rows] == ["+0.50R", "+0.50R"]
    assert dict(portfolio.totals)["R não realizado"] == "+1.00R"
    assert real_portfolio_message(load("portfolio_real")) == REAL_PHASE_0_MESSAGE
    watchlist, orders = load("watchlist")["watchlist"], load("orders")["orders"]
    assert [(row["Tipo"], row["Nível"], row["Direção"]) for row in rule_rows(watchlist[0]["rules"])] == [
        ("PRICE_CROSS", "101.5", "ABOVE")]
    assert market_tickers(watchlist, orders) == ["AAPL", "MSFT"]
    pairs = replay_pairs(orders, load("orders_replay")["orders"])
    assert len(pairs) == 2 and {pair["Diferença (R)"] for pair in pairs} == {"+0.00R"}
    view = health_view(load("health"))
    assert view.state == "DEGRADED" and view.frozen_orders == 0
    assert [cause["Código"] for cause in view.causes] == [
        "OPENING_MISSING", "NEEDS_REVIEW_QUEUE", "UNDELIVERABLE_ALERTS"]
    assert "1 alerta(s) expirado(s) sem entrega nos últimos 7 dias (n8n fora do caminho crítico)." in view.info_notices
    overview = load("quality_overview")
    assert {"2025-11-25", "2025-11-26"} <= {row["Pregão"] for row in coverage_rows(overview)}
    assert [(row["Ticker"], row["Minutos"]) for row in gap_rows(overview)] == [("MSFT", "35")]
    assert [row["Último resultado"] for row in outbox_rows(load("alert_outbox")["alerts"])] == ["EXPIRED"]
    log = [(row["Estado"], row["Causas"]) for row in health_log_rows(load("health_log")["entries"])]
    assert ("DEGRADED", "LIVE_CYCLE_STALE") in log


def test_the_client_reads_the_recorded_observation_responses():
    report = serving("observation_report").observation_report(date(2025, 11, 25))
    assert (report["session_day"], report["trades"]["closed"], report["trades"]["replay_closed"]) == (
        "2025-11-25", 2, 2)
    assert report["pressure"]["estimate"] is True and report["pressure"]["method"] == "OHLCV_PRESSURE_ESTIMATE_V1"
    summary = serving("observation_summary").observation_summary(date(2025, 11, 25), date(2025, 11, 26))
    assert [day["session_day"] for day in summary["days"]] == ["2025-11-25", "2025-11-26"]
