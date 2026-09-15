"""AppTest smoke over the real entry point with a fake API client (D40). No server, no network."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.client import ApiRequestFailed, ApiUnreachable

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")  # dashboard/dashboard/app.py
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "api"  # recorded from the real API (D57)
TS = "2025-11-25T15:30:00+00:00"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"), parse_float=Decimal)


ORDERS = _load("orders")["orders"]
ORDERS_REPLAY = _load("orders_replay")["orders"]
ORDER_DETAIL = _load("order_detail")
ORDER_CHART = _load("order_chart")
MARKET_BARS = _load("market_bars")
METRICS_BY_ORIGIN = _load("metrics_by_origin")
METRICS_GENERIC = _load("metrics")
OBSERVATION_REPORT = _load("observation_report")
OBSERVATION_SUMMARY = _load("observation_summary")
DETAIL_ORDER_ID = str(ORDER_DETAIL["order"]["order_id"])  # the only order id with recorded detail/chart fixtures
SUMMARY = {
    "trades": 12, "win_rate": Decimal("0.5833"), "avg_r": Decimal("0.42"), "expectancy_r": Decimal("0.42"),
    "profit_factor": Decimal("1.8"), "max_drawdown_r": Decimal("2.5"), "avg_duration_seconds": Decimal("3600"),
    "avg_mfe_r": Decimal("1.1"), "avg_mae_r": Decimal("-0.6"), "execution_rate": Decimal("0.75"),
    "win_rate_ci": [Decimal("0.3333"), Decimal("0.8333")], "expectancy_ci": [Decimal("-0.1"), Decimal("0.95")],
    "drawdown_sequence_risk": None, "excluded_needs_review": {"count": 1, "reasons": {"MANUAL": 1}},
    "included_needs_review": {"count": 0, "reasons": {}}, "warnings": ["INSUFFICIENT_SAMPLE"],
}
SIGNAL = {
    "signal_id": "s-1", "ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE", "strategy_version": "1.0",
    "entry_zone_low": "100", "entry_zone_high": "102", "stop": "97", "target1": "106", "target2": "110",
    "trigger_price": None, "auto_order_id": "o-auto", "auto_order_status": "PENDING", "created_at": TS,
    "valid_until_ts": "2025-11-27T21:00:00+00:00",
}
PRESSURE = {
    "ticker": "MSFT", "price_source": "alpaca_iex", "data_as_of": TS, "window_bars": 30, "estimate": True,
    "method": "OHLCV_PRESSURE_ESTIMATE_V1",
    "disclaimer": "Estimate derived from 1-minute OHLCV bars; it is not order-flow or trade-side data.",
    "available": False, "reason": "INSUFFICIENT_BARS", "values": None, "cmf_threshold": None, "side": None,
}


class FakeApi:
    def __init__(self) -> None:
        self.manual_calls: list[str] = []
        self.manual_error: Exception | None = None
        self.observation_error: Exception | None = None

    def metrics(self, **kwargs):
        group_by = kwargs.get("group_by")
        if group_by is None:  # overview.render: the hand-built SUMMARY keeps the review/warning assertions exact
            return {"groups": [{"key": None, "summary": SUMMARY}]}
        return METRICS_BY_ORIGIN if group_by == "origin" else METRICS_GENERIC

    def orders(self, **kwargs):
        return ORDERS_REPLAY if kwargs.get("replay") else ORDERS

    def signals(self, **kwargs):
        return [SIGNAL]

    def create_manual_order(self, signal_id):
        self.manual_calls.append(signal_id)
        if self.manual_error is not None:
            raise self.manual_error
        return {"order_id": "o-manual-1", "actionability_run_id": "run", "data_as_of": TS,
                "partial_bar_skipped": False}

    def health(self):
        return {"state": "HEALTHY", "causes": [
            {"code": "UNDELIVERABLE_ALERTS", "severity": "INFO", "detail": {"count": 2}},
            {"code": "ORDER_EVENT_ALERTS_BEHIND", "severity": "INFO", "detail": {"count": 1}},
        ], "facts": {"live_runs": [], "frozen_orders": 0, "incidents_total": 0, "incident_groups": [],
                     "needs_review": {"MANUAL": 1}, "missing_runs": {}}}

    def quality_overview(self):
        return {"window_days": 14, "coverage": [], "data_gaps": []}

    def alert_outbox(self, **kwargs):
        return [{"alert_key": "HEALTH:9", "kind": "HEALTH", "created_at": TS, "failures": 3,
                 "last_outcome": "EXPIRED", "last_status_code": None, "last_error_type": "ConnectTimeout"}]

    def health_log(self, **kwargs):
        return [{"id": 3, "state": "DEGRADED", "cause_codes": ["LIVE_CYCLE_STALE"], "observed_at": TS}]

    def watchlist(self):
        return [{"ticker": "MSFT", "added_at": TS, "rules": []}]

    def order_detail(self, order_id):
        if order_id != DETAIL_ORDER_ID:  # only this order id has a recorded detail fixture (D57)
            raise LookupError(order_id)
        return ORDER_DETAIL

    def order_chart(self, order_id):
        if order_id != DETAIL_ORDER_ID:  # ditto for the chart fixture
            raise LookupError(order_id)
        return ORDER_CHART

    def market_bars(self, ticker, start, end, *, source=None):
        return MARKET_BARS

    def pressure(self, ticker, **kwargs):
        return PRESSURE

    def virtual_portfolio(self):
        return {"kind": "VIRTUAL", "data_as_of": TS, "portfolio": {
            "basis": "PER_1R_NORMALIZED", "disclaimer": "Virtual positions sized per 1R of risk.", "positions": [],
            "totals": {"positions": 0, "marked": 0, "unmarked": 0, "notional": "0", "unrealized_pnl": "0",
                       "unrealized_r": "0", "risk_amount": "0"}}}

    def real_portfolio(self):
        return {"kind": "REAL", "available": False, "reason": "PHASE_0_PENDING", "source": None, "positions": []}

    def observation_report(self, day):
        if self.observation_error is not None:
            raise self.observation_error
        return OBSERVATION_REPORT

    def observation_summary(self, start, end):
        return OBSERVATION_SUMMARY


def open_page(fake, page):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["api_client"] = fake
    at.run()
    assert not at.exception
    if page != "Visão geral":
        at.radio(key="page").set_value(page).run()
        assert not at.exception
    return at


def values(elements):
    return [element.value for element in elements]


def test_overview_shows_cards_warnings_and_review_exclusions():
    at = open_page(FakeApi(), "Visão geral")
    assert at.title[0].value == "Visão geral"
    assert {metric.label for metric in at.metric} >= {"Trades", "Win rate", "Expectância (R)", "Taxa de execução"}
    assert "Amostra insuficiente (menos de 30 trades)." in values(at.warning)
    assert "Excluídas por revisão: 1 (MANUAL: 1)" in values(at.caption)


def test_buy_virtual_creates_a_manual_order():
    fake = FakeApi()
    at = open_page(fake, "Sinais do dia")
    at.button(key="buy-s-1").click().run()
    assert fake.manual_calls == ["s-1"]
    assert any("o-manual-1" in value for value in values(at.success))


@pytest.mark.parametrize("error, message", [
    (ApiRequestFailed(422, "SIGNAL_EXPIRED"), "Sinal expirado: a ordem virtual não foi criada."),
    (ApiRequestFailed(422, "SIGNAL_NO_LONGER_ACTIONABLE", "STOPPED"),
     "Sinal não é mais acionável: a ordem virtual não foi criada. Motivo: a ordem hipotética já foi estopada."),
    (ApiRequestFailed(503, "ACTIONABILITY_UNVERIFIABLE"),
     "Não foi possível verificar a actionability (dados indisponíveis). Tente de novo mais tarde."),
    (ApiUnreachable("ConnectError"), "API indisponível (ConnectError)."),
])
def test_buy_virtual_errors_are_shown_explicitly_without_a_traceback(error, message):
    fake = FakeApi()
    fake.manual_error = error
    at = open_page(fake, "Sinais do dia")
    at.button(key="buy-s-1").click().run()
    assert not at.exception and values(at.error) == [message]


def test_health_page_shows_info_causes_expired_alerts_and_the_last_health_log():
    at = open_page(FakeApi(), "Saúde")
    assert "Estado: HEALTHY" in values(at.success)
    assert "2 alerta(s) expirado(s) sem entrega nos últimos 7 dias (n8n fora do caminho crítico)." in values(at.info)
    assert "1 evento(s) de ordem ficaram para trás do lookback de alertas." in values(at.info)
    assert any("DEGRADED" in value and "LIVE_CYCLE_STALE" in value for value in values(at.markdown))


def test_portfolio_page_shows_the_phase_0_message_and_never_a_combined_total():
    at = open_page(FakeApi(), "Portfólio")
    assert "Portfólio real indisponível (Fase 0 pendente)." in values(at.info)
    assert "Totais do portfólio real e do virtual nunca são somados." in values(at.caption)


def test_market_page_always_labels_pressure_as_an_estimate():
    at = open_page(FakeApi(), "Mercado")
    captions = values(at.caption)
    assert "Método: OHLCV_PRESSURE_ESTIMATE_V1" in captions
    assert PRESSURE["disclaimer"] in captions


def test_watchlist_page_lists_tickers():
    at = open_page(FakeApi(), "Watchlist e alertas")
    assert at.title[0].value == "Watchlist e regras de alerta" and not at.exception


def test_watchlist_page_shows_the_empty_message_only_when_the_call_actually_succeeded():
    class EmptyWatchlist(FakeApi):
        def watchlist(self):
            return []

    at = open_page(EmptyWatchlist(), "Watchlist e alertas")
    assert "Watchlist vazia." in values(at.info)

    class FailingWatchlist(FakeApi):
        def watchlist(self):
            raise ApiUnreachable("ConnectError")

    at = open_page(FailingWatchlist(), "Watchlist e alertas")
    assert not at.exception
    assert "Watchlist vazia." not in values(at.info)  # M7: a failed call must not read as an empty watchlist
    assert "API indisponível (ConnectError)." in values(at.error)


def test_health_page_shows_a_caption_when_the_log_is_empty():
    class EmptyLog(FakeApi):
        def health_log(self, **kwargs):
            return []

    at = open_page(EmptyLog(), "Saúde")
    assert "Nenhuma transição registrada." in values(at.caption)


def test_orders_detail_page_renders_the_chart_quality_section_and_event_log():
    at = open_page(FakeApi(), "Ordens")
    assert len(at.dataframe[0].value) == 3  # the recorded orders list, unfiltered
    at.selectbox(key="orders-detail").select(DETAIL_ORDER_ID).run()
    assert not at.exception
    assert any("fonte fake_feed" in value for value in values(at.caption))  # the candlestick chart's caption
    assert any("Minutos esperados: 201" in value for value in values(at.markdown))  # the quality section
    assert "Nenhum recheck." in values(at.caption)  # the recorded fixture has no rechecks for this order
    event_types = {row["Tipo"] for frame in at.dataframe for row in frame.value.to_dict(orient="records")
                  if "Tipo" in row}
    assert {"ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT", "DATA_QUALITY"} <= event_types


def test_comparison_page_renders_group_tables_and_a_replay_pair_with_its_chart():
    at = open_page(FakeApi(), "Comparação")
    rows = [row for frame in at.dataframe for row in frame.value.to_dict(orient="records")]
    assert any(row.get("Grupo") == "AUTO_STRATEGY" for row in rows)  # the "origin" comparison table (metrics_by_origin)
    assert any(row.get("Diferença (R)") == "+0.00R" for row in rows)  # the original x replay table
    assert "**Original** · v1" in values(at.markdown)  # the recorded chart for the first pair's original order


def test_missing_configuration_names_the_variables_only(monkeypatch):
    monkeypatch.delenv("DASHBOARD_API_URL", raising=False)
    monkeypatch.setenv("API_KEY", "secret-value")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert values(at.error) == ["Configuração do dashboard incompleta: MISSING:DASHBOARD_API_URL"]
    assert "secret-value" not in str(values(at.error))


def test_observation_page_labels_the_pressure_estimate_and_shows_the_summary():
    at = open_page(FakeApi(), "Observação")
    assert at.title[0].value == "Observação"
    assert {metric.label for metric in at.metric} >= {
        "Falhas de provider", "Minutos ausentes", "503 de actionability", "Linhas de recheck", "Falhas de entrega",
        "Reinícios do worker", "Transições de saúde", "Trades fechados", "R somado", "Latência mediana"}
    captions = values(at.caption)
    assert "Método: OHLCV_PRESSURE_ESTIMATE_V1" in captions
    assert OBSERVATION_REPORT["pressure"]["disclaimer"] in captions
    assert OBSERVATION_REPORT["pressure"]["association_note"] in captions
    assert "Janela completa" in captions
    assert "Resumo: 2 pregão(ões)" in values(at.subheader)


def test_observation_page_shows_an_invalid_day_without_a_traceback():
    fake = FakeApi()
    fake.observation_error = ApiRequestFailed(422, "OBSERVATION_REQUEST_INVALID",
                                              detail={"errors": ["NOT_A_SESSION:2025-11-29"]})
    at = open_page(fake, "Observação")
    assert not at.exception
    assert values(at.error) == ["Pedido de observação inválido. Códigos: NOT_A_SESSION:2025-11-29."]
    assert "Resumo: 2 pregão(ões)" in values(at.subheader)  # the summary call still succeeds on its own
