from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from dashboard.client import ApiRequestFailed, ApiUnreachable
from dashboard.viewmodels import (
    CURVE_LIMIT_NOTICE,
    EMPTY,
    InputError,
    comparison_rows,
    coverage_rows,
    cumulative_r,
    curve_limit_notice,
    describe_api_error,
    health_view,
    market_day_window,
    market_tickers,
    metric_cards,
    order_rows,
    pressure_display,
    quality_view,
    real_portfolio_message,
    replay_pairs,
    review_exclusion_text,
    rule_body,
    signal_rows,
    summary_warnings,
    virtual_portfolio_view,
    watchlist_ticker,
)

ET = ZoneInfo("America/New_York")
SUMMARY = {
    "trades": 12, "win_rate": Decimal("0.5833"), "avg_r": Decimal("0.42"), "expectancy_r": Decimal("0.42"),
    "profit_factor": Decimal("1.8"), "max_drawdown_r": Decimal("2.5"), "avg_duration_seconds": Decimal("3600"),
    "avg_mfe_r": Decimal("1.1"), "avg_mae_r": Decimal("-0.6"), "execution_rate": Decimal("0.75"),
    "win_rate_ci": [Decimal("0.3333"), Decimal("0.8333")], "expectancy_ci": [Decimal("-0.1"), Decimal("0.95")],
    "drawdown_sequence_risk": {"p5": Decimal("1.2"), "p50": Decimal("2.4"), "p95": Decimal("4.75")},
    "excluded_needs_review": {"count": 2, "reasons": {"MISSING_BAR_UNVERIFIABLE": 1, "MANUAL": 1}},
    "included_needs_review": {"count": 0, "reasons": {}},
    "warnings": ["INSUFFICIENT_SAMPLE", "SHORT_BORROW_NOT_SIMULATED"],
}


def test_api_errors_are_described_with_fixed_messages_only():
    no_longer = ApiRequestFailed(422, "SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED")
    assert describe_api_error(no_longer) == (
        "Sinal não é mais acionável: a ordem virtual não foi criada. "
        "Motivo: a entrada hipotética já ocorreu antes do clique.")
    assert describe_api_error(ApiRequestFailed(503, "ACTIONABILITY_UNVERIFIABLE", None, {"ingest_error": "x"})) == (
        "Não foi possível verificar a actionability (dados indisponíveis). Tente de novo mais tarde.")
    assert describe_api_error(ApiRequestFailed(422, "ALERT_RULE_INVALID", None,
                                               {"errors": ["MISSING_FIELD:level", {"loc": ["x"]}]})) == (
        "Regra de alerta inválida. Códigos: MISSING_FIELD:level.")
    assert describe_api_error(ApiRequestFailed(418, "TEAPOT")) == "Erro da API: TEAPOT (HTTP 418)."
    assert describe_api_error(ApiUnreachable("ConnectError")) == "API indisponível (ConnectError)."
    assert describe_api_error(RuntimeError("secret text")) == "Erro inesperado (RuntimeError)."


def test_metric_cards_show_values_and_confidence_intervals():
    cards = {card.label: (card.value, card.interval) for card in metric_cards(SUMMARY)}
    assert cards["Trades"] == ("12", None)
    assert cards["Win rate"] == ("58.3%", "IC 95%: 33.3% a 83.3%")
    assert cards["Expectância (R)"] == ("+0.42R", "IC 95%: -0.10R a +0.95R")
    assert cards["Profit factor"] == ("1.80", None)
    assert cards["Drawdown máx. (R)"] == ("2.50R", None)
    assert cards["Taxa de execução"] == ("75.0%", None)
    assert cards["MAE médio (R)"] == ("-0.60R", None)
    assert cards["Risco de sequência (DD p5/p50/p95)"][0] == "1.20 / 2.40 / 4.75 R"
    assert summary_warnings(SUMMARY) == ["Amostra insuficiente (menos de 30 trades).",
                                         "SHORT: borrow, locate e custo de empréstimo não são simulados."]
    assert review_exclusion_text(SUMMARY) == "Excluídas por revisão: 2 (MANUAL: 1, MISSING_BAR_UNVERIFIABLE: 1)"
    empty = {**SUMMARY, "win_rate": None, "win_rate_ci": None, "drawdown_sequence_risk": None}
    cards = {card.label: (card.value, card.interval) for card in metric_cards(empty)}
    assert cards["Win rate"] == (EMPTY, None) and cards["Risco de sequência (DD p5/p50/p95)"][0] == EMPTY


def test_cumulative_r_follows_the_metrics_review_policy_and_order():
    orders = [
        {"order_id": "a", "status": "CLOSED", "closed_at": "2025-11-25T18:00:00+00:00", "r_multiple": "1.75",
         "needs_review": False},
        {"order_id": "b", "status": "CLOSED", "closed_at": "2025-11-25T17:00:00+00:00", "r_multiple": "-1",
         "needs_review": False},
        {"order_id": "c", "status": "CLOSED", "closed_at": "2025-11-25T17:30:00+00:00", "r_multiple": "0.5",
         "needs_review": True},
        {"order_id": "d", "status": "OPEN", "closed_at": None, "r_multiple": "0", "needs_review": False},
    ]
    assert [(p.order_id, p.cumulative_r) for p in cumulative_r(orders, include_needs_review=False)] == [
        ("b", Decimal("-1")), ("a", Decimal("0.75"))]
    assert [(p.order_id, p.cumulative_r) for p in cumulative_r(orders, include_needs_review=True)] == [
        ("b", Decimal("-1")), ("c", Decimal("-0.5")), ("a", Decimal("1.25"))]


def test_signal_and_order_rows_are_display_strings():
    signal = {"signal_id": "s-1", "ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE",
              "strategy_version": "1.0", "entry_zone_low": "100", "entry_zone_high": "102", "stop": "97",
              "target1": "106", "target2": "110", "trigger_price": None, "auto_order_status": None,
              "created_at": "2025-11-25T14:00:00+00:00", "valid_until_ts": "2025-11-27T21:00:00+00:00"}
    (row,) = signal_rows([signal])
    assert row == {"Ticker": "AAPL", "Direção": "LONG", "Estratégia": "REXSHARE 1.0", "Zona": "100–102",
                   "Stop": "97", "Alvos": "106 / 110", "Gatilho": EMPTY, "Status AUTO": "sem ordem AUTO",
                   "Criado": "2025-11-25 09:00 ET", "Válido até": "2025-11-27 16:00 ET"}
    order = {"order_id": "0123456789ab", "ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE",
             "origin": "MANUAL_USER", "status": "PENDING", "entry_path": None, "avg_entry": None, "r_multiple": "0",
             "needs_review": True, "frozen": False, "replay": False, "created_at": "2025-11-25T15:00:30+00:00"}
    (row,) = order_rows([order])
    assert (row["Ordem"], row["Status"], row["R"], row["Revisão"], row["entry_path"], row["Criada"]) == (
        "01234567", "PENDING", EMPTY, "sim", EMPTY, "2025-11-25 10:00 ET")


def test_quality_view_shows_coverage_events_and_rechecks():
    detail = {"data_quality": {
        "expected_bars": 390, "missing_bars": 35,
        "events": [{"type": "DATA_GAP", "event_key": "DATA_GAP:x",
                    "payload": {"gap_start_ts": "2025-11-25T15:10:00+00:00", "minutes": 35}}],
        "rechecks": [{"session_date": "2025-11-25", "data_as_of": "2025-11-26T21:45:00+00:00",
                      "payload": {"status": "PROVIDER_FAILURE_FINAL", "terminal_reason": "PROVIDER_FAILURE",
                                  "level_touch_check": None, "review_flags": []}}],
    }}
    view = quality_view(detail)
    assert (view.expected_bars, view.missing_bars, view.coverage) == (390, 35, "91.03%")
    assert view.events == [{"Tipo": "DATA_GAP", "event_key": "DATA_GAP:x",
                            "Detalhe": "lacuna de 35 min desde 2025-11-25 10:10 ET"}]
    assert view.rechecks == [{"Pregão": "2025-11-25", "Status": "PROVIDER_FAILURE_FINAL",
                              "Motivo terminal": "PROVIDER_FAILURE", "Toque de nível": EMPTY, "Revisões": "0",
                              "data_as_of": "2025-11-26 16:45 ET"}]
    assert quality_view({"data_quality": {"expected_bars": 0, "missing_bars": 0, "events": [],
                                          "rechecks": []}}).coverage == EMPTY


def test_health_view_surfaces_info_causes_review_queue_and_missing_runs():
    report = {"state": "DEGRADED", "causes": [
        {"code": "END_OF_DAY_MISSING", "severity": "DEGRADED", "detail": {"session_days": ["2025-11-26"]}},
        {"code": "UNDELIVERABLE_ALERTS", "severity": "INFO", "detail": {"count": 2}},
        {"code": "ORDER_EVENT_ALERTS_BEHIND", "severity": "INFO", "detail": {"count": 1}},
    ], "facts": {
        "live_runs": [{"status": "COMPLETED", "started_at": "2025-11-25T15:30:05+00:00",
                       "detail": {"market_now": "2025-11-25T15:30:00+00:00"}}],
        "frozen_orders": 1, "incidents_total": 3, "needs_review": {"MANUAL": 2, "DATA_GAP": 1},
        "incident_groups": [{"kind": "PROJECTION_INTEGRITY_ERROR", "reason": None, "occurrences": 2,
                             "affected_count": 1, "last_recorded_at": "2025-11-25T16:00:00+00:00"}],
        "missing_runs": {"END_OF_DAY": ["2025-11-26"]},
    }}
    view = health_view(report)
    assert view.state == "DEGRADED"
    assert view.causes[0] == {"Código": "END_OF_DAY_MISSING", "Severidade": "DEGRADED"}
    assert view.info_notices == [
        "2 alerta(s) expirado(s) sem entrega nos últimos 7 dias (n8n fora do caminho crítico).",
        "1 evento(s) de ordem ficaram para trás do lookback de alertas.",
    ]
    assert view.last_cycle == "COMPLETED · market_now 2025-11-25 10:30 ET · iniciado 2025-11-25 10:30 ET"
    assert (view.frozen_orders, view.incidents_total) == (1, 3)
    assert view.review_queue == [{"Motivo": "DATA_GAP", "Ordens": "1"}, {"Motivo": "MANUAL", "Ordens": "2"}]
    assert view.incidents[0]["Tipo"] == "PROJECTION_INTEGRITY_ERROR" and view.incidents[0]["Motivo"] == EMPTY
    assert view.missing_runs == ["END_OF_DAY: 2025-11-26"]
    down = health_view({"state": "UNHEALTHY", "causes": [
        {"code": "DATABASE_UNAVAILABLE", "severity": "UNHEALTHY", "detail": {"error": "OperationalError"}}],
        "facts": None})
    assert (down.last_cycle, down.frozen_orders, down.review_queue) == (
        "sem dados (banco ou schema indisponível)", None, [])


def test_comparison_and_replay_pairs():
    (row,) = comparison_rows([{"key": None, "summary": SUMMARY}])
    assert (row["Grupo"], row["Trades"], row["Expectância"], row["Excluídas (revisão)"]) == (
        "(sem valor)", "12", "+0.42R", "2")
    originals = [{"order_id": "o-1", "status": "CLOSED", "r_multiple": "1.75", "fill_model_version": "v1"}]
    replays = [
        {"order_id": "r-1", "replay_of_order_id": "o-1", "replay_mode": "RECALCULATE", "status": "CLOSED",
         "r_multiple": "1.5", "fill_model_version": "v1", "created_at": "2025-11-26T10:00:00+00:00"},
        {"order_id": "r-2", "replay_of_order_id": "gone", "replay_mode": "REPRODUCE", "status": "CLOSED",
         "r_multiple": "1", "fill_model_version": "v1", "created_at": "2025-11-26T11:00:00+00:00"},
    ]
    assert replay_pairs(originals, replays) == [{
        "Original": "o-1", "Replay": "r-1", "Modo": "RECALCULATE", "Status original": "CLOSED",
        "Status replay": "CLOSED", "R original": "+1.75R", "R replay": "+1.50R", "Diferença (R)": "-0.25R",
        "Fill model": "v1 → v1",
    }]


def test_portfolio_views_never_mix_real_and_virtual():
    virtual = {"portfolio": {"basis": "PER_1R_NORMALIZED", "disclaimer": "per 1R", "positions": [{
        "position": {"ticker": "AAPL", "direction": "LONG", "strategy": "REXSHARE", "origin": "AUTO_STRATEGY",
                     "qty_open": "25", "avg_entry": "101", "last_close": "103",
                     "last_close_ts": "2025-11-25T15:29:00+00:00", "frozen": False},
        "notional": "2575", "unrealized_pnl": "50", "unrealized_r": "0.5", "open_r": "0.5", "allocation_pct": "100",
    }], "totals": {"positions": 1, "marked": 1, "unmarked": 0, "notional": "2575", "unrealized_pnl": "50",
                   "unrealized_r": "0.5", "risk_amount": "100"}}}
    view = virtual_portfolio_view(virtual)
    assert view.disclaimer == "per 1R"
    assert (view.rows[0]["P&L não realizado"], view.rows[0]["R não realizado"], view.rows[0]["Alocação"]) == (
        "$50.00", "+0.50R", "100.00%")
    assert view.totals == [("Posições", "1"), ("Sem marcação", "0"), ("Notional (por 1R)", "$2,575.00"),
                           ("P&L não realizado (por 1R)", "$50.00"), ("R não realizado", "+0.50R")]
    assert real_portfolio_message({"available": False, "reason": "PHASE_0_PENDING"}) == (
        "Portfólio real indisponível (Fase 0 pendente).")
    assert real_portfolio_message({"available": False, "reason": "SOURCE_UNAVAILABLE", "error": "RuntimeError"}) == (
        "Portfólio real indisponível (SOURCE_UNAVAILABLE: RuntimeError).")
    assert real_portfolio_message({"available": True, "positions": []}) is None
    assert real_portfolio_message({"available": False}) == "Portfólio real indisponível (motivo não informado)."


def test_pressure_display_always_carries_method_and_disclaimer():
    unavailable = pressure_display({"estimate": True, "method": "OHLCV_PRESSURE_ESTIMATE_V1",
                                    "disclaimer": "not order flow", "available": False,
                                    "reason": "INSUFFICIENT_BARS"})
    assert (unavailable.method, unavailable.disclaimer, unavailable.lines) == (
        "OHLCV_PRESSURE_ESTIMATE_V1", "not order flow", ["Indisponível: INSUFFICIENT_BARS"])
    assert "estimativa" in unavailable.title.lower()
    shown = pressure_display({"method": "OHLCV_PRESSURE_ESTIMATE_V1", "disclaimer": "d", "available": True,
                              "side": "BUY", "cmf_threshold": "0.01", "values": {
                                  "bars": 30, "last_bar_ts": "2025-11-25T17:50:00+00:00",
                                  "chaikin_money_flow": "0.0137", "obv_slope": "0.0345", "vwap": "107.0922",
                                  "vwap_distance_pct": "2.7152", "close_location_value": "0.4118"}})
    assert shown.lines == [
        "Janela: 30 candles até 2025-11-25 12:50 ET", "CMF: 0.0137", "Inclinação do OBV: 0.0345",
        "VWAP da janela: 107.0922 (distância 2.7152%)", "CLV do último candle: 0.4118",
        "Pressão forte estimada: BUY (limiar CMF 0.01)",
    ]
    assert pressure_display({"available": False, "reason": "NO_BARS"}).method == "método não informado"


def test_rule_bodies_parse_decimal_text_and_report_codes():
    assert rule_body(kind="PRICE_CROSS", level=" 101,50 ", direction="ABOVE", cmf_threshold="", window_bars=30,
                     cooldown_minutes=15) == {"kind": "PRICE_CROSS", "cooldown_minutes": 15,
                                              "level": Decimal("101.50"), "direction": "ABOVE"}
    assert rule_body(kind="PRESSURE", level="", direction="ABOVE", cmf_threshold="0.3", window_bars=20,
                     cooldown_minutes=30) == {"kind": "PRESSURE", "cooldown_minutes": 30,
                                              "cmf_threshold": Decimal("0.3"), "window_bars": 20}
    with pytest.raises(InputError) as caught:
        rule_body(kind="PRICE_CROSS", level="abc", direction="ABOVE", cmf_threshold="", window_bars=30,
                  cooldown_minutes=30)
    assert caught.value.code == "INVALID_DECIMAL:level"


def test_market_helpers():
    start, end = market_day_window(date(2025, 11, 25))
    assert (start, end) == (datetime(2025, 11, 25, tzinfo=ET), datetime(2025, 11, 26, tzinfo=ET))
    assert market_tickers([{"ticker": "MSFT"}], [{"ticker": "AAPL"}, {"ticker": "MSFT"}]) == ["AAPL", "MSFT"]


def test_owner_facing_limits_and_labels():
    assert curve_limit_notice(999) is None
    assert curve_limit_notice(1000) == CURVE_LIMIT_NOTICE  # D59: the oldest closed orders are the ones dropped
    assert watchlist_ticker(" brk.b ") == "BRK.B"
    for bad in ("", "  ", "BRK/B", "BR K"):  # M7: "/" would be decoded by the server before routing
        with pytest.raises(InputError) as caught:
            watchlist_ticker(bad)
        assert caught.value.code == "TICKER_INVALID"
    (row,) = coverage_rows({"coverage": [{"session_date": "2025-11-25", "orders": 2, "expected_bars": 780,
                                          "missing_bars": 35, "coverage_pct": "95.51"}]})
    assert row == {"Pregão": "2025-11-25", "Ordens": "2", "Minutos-ordem esperados": "780",
                   "Minutos-ordem ausentes": "35", "Cobertura": "95.51%"}  # M8: summed per order
