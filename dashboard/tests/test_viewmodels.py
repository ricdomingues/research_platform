from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import dashboard.viewmodels as vm
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
    fmt_duration,
    health_view,
    market_day_window,
    market_tickers,
    metric_cards,
    observation_day_rows,
    observation_view,
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


OBSERVATION_REPORT = {
    "session_day": "2025-11-25", "as_of": "2025-11-25T20:00:00+00:00", "complete": False,
    "provider_failures": {"total_failures": 4, "consecutive_live_max": 3, "by_run_kind": [
        {"run_kind": "LIVE", "codes": {"SOURCE_ERROR": 3}}, {"run_kind": "OPENING", "codes": {"SOURCE_ERROR": 1}}]},
    "data_quality": {"expected_bars": 390, "missing_bars": 35, "coverage_pct": "91.03", "gaps": 1, "gap_minutes": 35,
                     "not_evaluated": {"PROVIDER_FAILURE": 1}},
    "actionability": {"requests": 6, "unverifiable": 3,
                      "unverifiable_causes": {"MISSING_MINUTES": 2, "PROVIDER_FAILURE": 1}},
    "rechecks": {"rows": 2, "statuses": {"EVALUATED": 2}, "about_this_session": {}},
    "alerts": {"attempts": {"DELIVERED": 1, "FAILED": 3}, "failure_types": {"ConnectTimeout": 2, "HTTP_502": 1},
               "pending_at_end": 2},
    "worker": {"starts": 3, "restarts": 3, "unclean_ends": 1, "stops": {"LOCK_LOST": 1}, "open_session_at_end": True},
    "health": {"state_at_start": "HEALTHY", "state_at_end": "DEGRADED", "transitions": 2, "log_rows": 3,
               "seconds_by_state": {"DEGRADED": 900, "HEALTHY": 36000}},
    "trades": {"stats": {"trades": 1, "sum_r": "1.75"}, "excluded_needs_review": 1, "replay_closed": 2, "rows": [
        {"order_id": "0f3e2d1c-0000-4000-8000-000000000001", "ticker": "AAPL", "origin": "AUTO_STRATEGY",
         "closed_at": "2025-11-25T17:50:00+00:00", "r_multiple": "1.75", "mfe_r": "2.375", "mae_r": "-0.1",
         "needs_review": False, "pressure_alignment": "ALIGNED", "pressure_strength": "STRONG"}]},
    "latency": {
        "bar": {"count": 2, "median_seconds": 3900, "p90_seconds": 3900, "max_seconds": 3900},
        "recorded": {"count": 2, "median_seconds": 4000, "p90_seconds": 4100, "max_seconds": 4100},
        "bar_by_origin": {"AUTO_STRATEGY": {"count": 2, "median_seconds": 3900, "p90_seconds": 3900,
                                            "max_seconds": 3900}},
        "unfilled": {"EXPIRED": 1},
    },
    "pressure": {"method": "OHLCV_PRESSURE_ESTIMATE_V1", "disclaimer": "Estimate, not order flow.",
                 "association_note": "Descriptive counts, not causal.", "unavailable_reasons": {"NO_BARS": 1},
                 "buckets": [
                     {"alignment": "ALIGNED", "strength": "STRONG", "trades": 5, "wins": 4, "sum_r": "3.5",
                      "mean_r": "0.7"},
                     {"alignment": "UNAVAILABLE", "strength": None, "trades": 1, "wins": 0, "sum_r": "-1",
                      "mean_r": None}]},
}
OBSERVATION_SUMMARY = {"sessions": 2, "days": [
    {"session_day": "2025-11-25", "complete": True, "provider_failures": 4, "expected_bars": 390, "missing_bars": 35,
     "actionability_unverifiable": 3, "rechecks": 2, "alert_failures": 3, "alerts_expired": 0, "worker_restarts": 3,
     "unclean_worker_ends": 1, "health_transitions": 2, "fills": 2, "trades": 1, "sum_r": "1.75"},
    {"session_day": "2025-11-26", "complete": False, "provider_failures": 0, "expected_bars": 0, "missing_bars": 0,
     "actionability_unverifiable": 0, "rechecks": 0, "alert_failures": 0, "alerts_expired": 0, "worker_restarts": 0,
     "unclean_worker_ends": 0, "health_transitions": 0, "fills": 0, "trades": 0, "sum_r": "0"},
]}


def test_durations_are_shown_in_hours_minutes_and_seconds():
    assert (fmt_duration(None), fmt_duration(59), fmt_duration(3900), fmt_duration(36000)) == (
        EMPTY, "0m59s", "1h05m00s", "10h00m00s")


def test_the_observation_view_turns_every_section_into_display_strings():
    view = observation_view(OBSERVATION_REPORT)
    assert (view.title, view.status) == ("Pregão 2025-11-25", "Janela parcial (as-of 2025-11-25 15:00 ET)")
    cards = {card.label: card.value for card in view.cards}
    assert cards == {
        "Falhas de provider": "4", "Minutos ausentes": "35/390", "503 de actionability": "3",
        "Linhas de recheck": "2", "Falhas de entrega": "3", "Reinícios do worker": "3", "Transições de saúde": "2",
        "Trades fechados": "1", "R somado": "+1.75R", "Latência mediana": "1h05m00s",
    }
    details = {row["Métrica"]: (row["Valor"], row["Detalhe"]) for row in view.operations}
    assert details["Falhas de provider"] == ("4", "SOURCE_ERROR: 4 · maior sequência LIVE: 3")
    assert details["Candles ausentes"] == ("35/390", "cobertura 91.03% · DATA_GAP 1 (35 min) · não avaliadas: "
                                                     "PROVIDER_FAILURE: 1")
    assert details["503 de actionability"] == ("3/6", "MISSING_MINUTES: 2, PROVIDER_FAILURE: 1")
    assert details["Entrega de alertas"] == ("3 falha(s)", "tentativas: DELIVERED: 1, FAILED: 3 · tipos: "
                                                          "ConnectTimeout: 2, HTTP_502: 1 · pendentes no fim: 2")
    assert details["Worker"] == ("3 reinício(s)", "inícios 3 · fins sem parada 1 · paradas: LOCK_LOST: 1 · "
                                                  "sessão aberta no fim: sim")
    assert details["Saúde"] == ("2 transição(ões)", "HEALTHY → DEGRADED · linhas do log 3 · DEGRADED: 15m00s, "
                                                     "HEALTHY: 10h00m00s")
    assert details["DATA_QUALITY_RECHECK"] == ("2", "status: EVALUATED: 2 · sobre este pregão: —")
    assert view.trades == [{"Ordem": "0f3e2d1c", "Ticker": "AAPL", "Origem": "AUTO_STRATEGY",
                            "Fechada": "2025-11-25 12:50 ET", "R": "+1.75R", "MFE": "+2.38R", "MAE": "-0.10R",
                            "Revisão": "não", "Pressão (estimativa)": "a favor · forte"}]
    assert view.trades_caption == ("Excluídas por revisão: 1 · replay fechadas (fora das métricas): 2 · "
                                   "sem fill: EXPIRED: 1")
    assert [row["Medida"] for row in view.latency] == ["Candle do fill", "Gravação do fill", "Candle · AUTO_STRATEGY"]
    assert (view.latency[1]["Mediana"], view.latency[1]["p90"]) == ("1h06m40s", "1h08m20s")
    assert view.pressure == [  # n beside every mean; no mean below 5 trades (D66)
        {"Pressão": "a favor · forte", "Trades": "5", "Wins": "4", "R somado": "+3.50R", "R médio (n)": "+0.70R (n=5)"},
        {"Pressão": "indisponível", "Trades": "1", "Wins": "0", "R somado": "-1.00R", "R médio (n)": "— (n=1)"},
    ]
    assert (view.pressure_method, view.pressure_disclaimer, view.pressure_note) == (
        "OHLCV_PRESSURE_ESTIMATE_V1", "Estimate, not order flow.", "Descriptive counts, not causal.")


def test_summary_rows_and_the_observation_error_message():
    rows = observation_day_rows(OBSERVATION_SUMMARY)
    assert rows[0] == {"Pregão": "2025-11-25", "Completo": "sim", "Falhas de provider": "4", "Ausentes": "35/390",
                       "503": "3", "Rechecks": "2", "Falhas de alerta": "3", "Reinícios": "3", "Fins sem parada": "1",
                       "Transições": "2", "Fills": "2", "Trades": "1", "R": "+1.75R"}
    assert (rows[1]["Completo"], rows[1]["R"]) == ("não", "+0.00R")
    error = ApiRequestFailed(422, "OBSERVATION_REQUEST_INVALID", detail={"errors": ["NOT_A_SESSION:2025-11-29"]})
    assert describe_api_error(error) == "Pedido de observação inválido. Códigos: NOT_A_SESSION:2025-11-29."


# --- Manual-review panel (Plan 7) ---------------------------------------------------------------------

def _panel_payload(**overrides):
    row = {
        "signal_id": "s-1", "ticker": "MDT", "state": "ACTIONABLE", "reason": "ENTRY_WINDOW_OPEN",
        "strategy": "REXSHARE", "strategy_version": "1.0", "direction": "LONG",
        "entry_zone_low": Decimal("95.00"), "entry_zone_high": Decimal("95.60"), "stop": Decimal("94.80"),
        "target1": Decimal("97.10"), "target2": None, "valid_until_ts": "2026-09-24T20:00:00+00:00",
        "order_id": None,
    }
    row.update(overrides)
    return {"signals": [row]}


def test_actionable_pulses_green():
    (row,) = vm.panel_rows(_panel_payload())
    assert (row.tone, row.pulse) == ("actionable", True)
    assert row.reason == "Entrada ainda válida agora"


def test_stale_never_pulses_and_is_not_green():
    (row,) = vm.panel_rows(_panel_payload(state="STALE", reason="COVERAGE_UNVERIFIED"))
    assert (row.tone, row.pulse) == ("stale", False)
    assert row.reason == "Dados incompletos: nada pode ser afirmado"


def test_exit_pulses_and_keeps_the_domain_reason():
    (row,) = vm.panel_rows(_panel_payload(state="EXIT", reason="TARGET_FINAL", order_id="o-9"))
    assert (row.tone, row.pulse) == ("exit", True)
    assert row.reason == "Alvo atingido" and row.order_id == "o-9"


def test_an_unknown_state_renders_muted_and_never_pulses():
    """A state this dashboard has never heard of must fail towards saying nothing, never towards green."""
    (row,) = vm.panel_rows(_panel_payload(state="SOMETHING_NEW", reason="WHATEVER"))
    assert (row.tone, row.pulse) == ("stale", False)
    assert row.state_label == "SOMETHING_NEW" and row.reason == "WHATEVER"


def test_a_malformed_payload_yields_no_rows_rather_than_a_guess():
    assert vm.panel_rows({}) == []
    assert vm.panel_rows({"signals": None}) == []


def test_watch_lists_only_blocked_candidates_with_the_policy_codes():
    rows = vm.watch_rows([
        {"ticker": "AAPL", "pattern": "HAMMER", "direction": "LONG", "deterministic_score": Decimal("0.71"),
         "detected_at": "2026-09-24T18:00:00+00:00", "promotion_blockers": ["NOT_VALIDATED"]},
        {"ticker": "MSFT", "pattern": "ENGULFING", "direction": "LONG", "deterministic_score": Decimal("0.80"),
         "detected_at": "2026-09-24T18:00:00+00:00", "promotion_blockers": []},
    ])
    assert [row["Ativo"] for row in rows] == ["AAPL"]
    assert rows[0]["Bloqueios"] == "NOT_VALIDATED"
