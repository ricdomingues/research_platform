"""Pure mappers from API JSON to display values (D40). No Streamlit here, so every rule is unit-tested."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from dashboard.client import ApiRequestFailed, ApiUnreachable

ET = ZoneInfo("America/New_York")
EMPTY = "—"
SHORT_NOTICE = "SHORT: borrow, locate e custo de empréstimo não são simulados (spec 4.7)."
REAL_PHASE_0_MESSAGE = "Portfólio real indisponível (Fase 0 pendente)."
PRESSURE_TITLE = "Estimativa de pressão (OHLCV) — não é fluxo de ordens"
ORDER_LIST_LIMIT = 1000  # GET /orders maximum page size (D59)
CURVE_LIMIT_NOTICE = (
    "Curva limitada às 1000 ordens fechadas mais recentes (limite de GET /orders): as mais antigas ficaram de fora.")
WINDOW_TRUNCATED_NOTICE = "Janela do gráfico limitada a 7 dias a partir do dia de evaluation_start_ts (D42)."

ERROR_MESSAGES = {
    "SIGNAL_EXPIRED": "Sinal expirado: a ordem virtual não foi criada.",
    "SIGNAL_NO_LONGER_ACTIONABLE": "Sinal não é mais acionável: a ordem virtual não foi criada.",
    "ACTIONABILITY_UNVERIFIABLE": (
        "Não foi possível verificar a actionability (dados indisponíveis). Tente de novo mais tarde."),
    "SIGNAL_NOT_FOUND": "Sinal não encontrado.",
    "ORDER_NOT_FOUND": "Ordem não encontrada.",
    "TICKER_INVALID": "Ticker inválido.",
    "TICKER_NOT_TRADABLE": "Ticker não negociável.",
    "TICKER_UNVERIFIABLE": "Não foi possível verificar o ticker agora.",
    "ALERT_RULE_INVALID": "Regra de alerta inválida.",
    "WATCHLIST_TICKER_NOT_FOUND": "Ticker fora da watchlist.",
    "ALERT_RULE_NOT_FOUND": "Regra não encontrada.",
    "MARKET_REQUEST_INVALID": "Pedido de mercado inválido.",
    "REQUEST_INVALID": "Parâmetros inválidos.",
    "UNAUTHORIZED": "Chave da API recusada.",
    "INTERNAL_ERROR": "Erro interno da API.",
    "OBSERVATION_REQUEST_INVALID": "Pedido de observação inválido.",
}
REASON_MESSAGES = {
    "INVALIDATED": "a tese foi invalidada (stop sem posição ou zona perdida com CANCEL)",
    "ENTRY_OPPORTUNITY_ALREADY_OCCURRED": "a entrada hipotética já ocorreu antes do clique",
    "STOPPED": "a ordem hipotética já foi estopada",
    "TARGET_REACHED": "a ordem hipotética já atingiu o alvo final",
}
WARNING_MESSAGES = {
    "INSUFFICIENT_SAMPLE": "Amostra insuficiente (menos de 30 trades).",
    "SHORT_BORROW_NOT_SIMULATED": "SHORT: borrow, locate e custo de empréstimo não são simulados.",
}
INFO_NOTICES = {
    "UNDELIVERABLE_ALERTS": "{count} alerta(s) expirado(s) sem entrega nos últimos 7 dias (n8n fora do caminho crítico).",
    "ORDER_EVENT_ALERTS_BEHIND": "{count} evento(s) de ordem ficaram para trás do lookback de alertas.",
}


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def fmt_price(value: Any) -> str:
    return EMPTY if value is None else str(value)


def fmt_decimal(value: Any, places: int = 2) -> str:
    return EMPTY if value is None else f"{_decimal(value):.{places}f}"


def fmt_pct(value: Any) -> str:
    return EMPTY if value is None else f"{_decimal(value) * 100:.1f}%"


def fmt_r(value: Any) -> str:
    return EMPTY if value is None else f"{_decimal(value):+.2f}R"


def fmt_money(value: Any) -> str:
    return EMPTY if value is None else f"${_decimal(value):,.2f}"


def fmt_ts(value: Any) -> str:
    if value is None:
        return EMPTY
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def today_et() -> date:
    return datetime.now(ET).date()


def market_day_window(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, time(0), tzinfo=ET), datetime.combine(day + timedelta(days=1), time(0), tzinfo=ET)


def describe_api_error(exc: Exception) -> str:
    """Fixed Portuguese messages by code; reasons and detail.errors codes appended. Never exception text (D40)."""
    if isinstance(exc, ApiUnreachable):
        return f"API indisponível ({exc.error_type})."
    if not isinstance(exc, ApiRequestFailed):
        return f"Erro inesperado ({type(exc).__name__})."
    parts = [ERROR_MESSAGES.get(exc.code, f"Erro da API: {exc.code} (HTTP {exc.status_code}).")]
    if exc.reason:
        parts.append(f"Motivo: {REASON_MESSAGES.get(exc.reason, exc.reason)}.")
    errors = exc.detail.get("errors")
    codes = [item for item in errors if isinstance(item, str)] if isinstance(errors, list) else []
    if codes:
        parts.append(f"Códigos: {', '.join(codes)}.")
    return " ".join(parts)


@dataclass(frozen=True)
class MetricCard:
    label: str
    value: str
    interval: str | None


def _interval(bounds: Any, formatter: Any) -> str | None:
    if not bounds:
        return None
    low, high = bounds
    return f"IC 95%: {formatter(low)} a {formatter(high)}"


def metric_cards(summary: Mapping[str, Any]) -> list[MetricCard]:
    risk = summary.get("drawdown_sequence_risk")
    sequence = EMPTY if not risk else (
        f"{fmt_decimal(risk['p5'])} / {fmt_decimal(risk['p50'])} / {fmt_decimal(risk['p95'])} R")
    drawdown = summary.get("max_drawdown_r")
    return [
        MetricCard("Trades", str(summary["trades"]), None),
        MetricCard("Win rate", fmt_pct(summary.get("win_rate")), _interval(summary.get("win_rate_ci"), fmt_pct)),
        MetricCard("Expectância (R)", fmt_r(summary.get("expectancy_r")),
                   _interval(summary.get("expectancy_ci"), fmt_r)),
        MetricCard("R médio", fmt_r(summary.get("avg_r")), None),
        MetricCard("Profit factor", fmt_decimal(summary.get("profit_factor")), None),
        MetricCard("Drawdown máx. (R)", EMPTY if drawdown is None else f"{fmt_decimal(drawdown)}R", None),
        MetricCard("Taxa de execução", fmt_pct(summary.get("execution_rate")), None),
        MetricCard("MFE médio (R)", fmt_r(summary.get("avg_mfe_r")), None),
        MetricCard("MAE médio (R)", fmt_r(summary.get("avg_mae_r")), None),
        MetricCard("Risco de sequência (DD p5/p50/p95)", sequence,
                   "Permutação da ordem dos trades: risco de sequência, não IC do drawdown histórico."),
    ]


def summary_warnings(summary: Mapping[str, Any]) -> list[str]:
    return [WARNING_MESSAGES.get(code, code) for code in summary.get("warnings", [])]


def review_exclusion_text(summary: Mapping[str, Any]) -> str:
    excluded, included = summary["excluded_needs_review"], summary["included_needs_review"]
    text = f"Excluídas por revisão: {excluded['count']}"
    if excluded["reasons"]:
        text += " (" + ", ".join(f"{name}: {count}" for name, count in sorted(excluded["reasons"].items())) + ")"
    if included["count"]:
        text += f" · incluídas em revisão: {included['count']}"
    return text


@dataclass(frozen=True)
class RPoint:
    closed_at: str
    cumulative_r: Decimal
    order_id: str


def cumulative_r(orders: Sequence[Mapping[str, Any]], *, include_needs_review: bool) -> list[RPoint]:
    """Same order and review policy as GET /metrics (spec 5.4): closed_at then order_id; flagged excluded by default."""
    closed = [
        order for order in orders
        if order.get("status") == "CLOSED" and order.get("closed_at") and order.get("r_multiple") is not None
        and (include_needs_review or not order.get("needs_review"))
    ]
    closed.sort(key=lambda order: (datetime.fromisoformat(str(order["closed_at"])), str(order["order_id"])))
    total = Decimal(0)
    points: list[RPoint] = []
    for order in closed:
        total += _decimal(order["r_multiple"])
        points.append(RPoint(str(order["closed_at"]), total, str(order["order_id"])))
    return points


def signal_rows(signals: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Ticker": str(s["ticker"]), "Direção": str(s["direction"]),
        "Estratégia": f"{s['strategy']} {s['strategy_version']}",
        "Zona": f"{fmt_price(s['entry_zone_low'])}–{fmt_price(s['entry_zone_high'])}",
        "Stop": fmt_price(s["stop"]),
        "Alvos": fmt_price(s["target1"]) if s.get("target2") is None else f"{s['target1']} / {s['target2']}",
        "Gatilho": fmt_price(s.get("trigger_price")),
        "Status AUTO": str(s["auto_order_status"]) if s.get("auto_order_status") else "sem ordem AUTO",
        "Criado": fmt_ts(s["created_at"]), "Válido até": fmt_ts(s["valid_until_ts"]),
    } for s in signals]


def order_rows(orders: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Ordem": str(o["order_id"])[:8], "Ticker": str(o["ticker"]), "Direção": str(o["direction"]),
        "Estratégia": str(o["strategy"]), "Origem": str(o["origin"]), "Status": str(o["status"]),
        "entry_path": fmt_price(o.get("entry_path")), "Entrada média": fmt_price(o.get("avg_entry")),
        "R": fmt_r(o.get("r_multiple")) if o.get("status") == "CLOSED" else EMPTY,
        "Revisão": "sim" if o.get("needs_review") else "não", "Frozen": "sim" if o.get("frozen") else "não",
        "Replay": "sim" if o.get("replay") else "não", "Criada": fmt_ts(o.get("created_at")),
    } for o in orders]


def order_label(order: Mapping[str, Any]) -> str:
    return f"{order['ticker']} · {order['status']} · {order['origin']} · {str(order['order_id'])[:8]}"


def event_log_rows(events: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "seq": str(e["seq"]), "Tipo": str(e["type"]), "event_key": str(e["event_key"]),
        "Candle": fmt_ts(e.get("bar_ts")), "Preço": fmt_price(e.get("price")), "Qtd": fmt_price(e.get("qty")),
    } for e in events]


@dataclass(frozen=True)
class QualityView:
    expected_bars: int
    missing_bars: int
    coverage: str
    events: list[dict[str, str]]
    rechecks: list[dict[str, str]]


def _quality_detail(event: Mapping[str, Any]) -> str:
    payload = event.get("payload") or {}
    if event.get("type") == "DATA_GAP":
        return f"lacuna de {payload.get('minutes')} min desde {fmt_ts(payload.get('gap_start_ts'))}"
    return f"{payload.get('expected_bars')} esperados, {payload.get('missing_bars')} ausentes"


def quality_view(detail: Mapping[str, Any]) -> QualityView:
    quality = detail["data_quality"]
    expected, missing = int(quality["expected_bars"]), int(quality["missing_bars"])
    coverage = EMPTY if expected == 0 else f"{Decimal(expected - missing) / Decimal(expected) * 100:.2f}%"
    events = [{"Tipo": str(e["type"]), "event_key": str(e["event_key"]), "Detalhe": _quality_detail(e)}
              for e in quality["events"]]
    rechecks = [{
        "Pregão": str(r["session_date"]), "Status": str(r["payload"].get("status") or EMPTY),
        "Motivo terminal": str(r["payload"].get("terminal_reason") or EMPTY),
        "Toque de nível": str(r["payload"].get("level_touch_check") or EMPTY),
        "Revisões": str(len(r["payload"].get("review_flags") or [])), "data_as_of": fmt_ts(r.get("data_as_of")),
    } for r in quality["rechecks"]]
    return QualityView(expected, missing, coverage, events, rechecks)


@dataclass(frozen=True)
class HealthView:
    state: str
    causes: list[dict[str, str]]
    info_notices: list[str]
    last_cycle: str
    frozen_orders: int | None
    incidents_total: int | None
    review_queue: list[dict[str, str]]
    incidents: list[dict[str, str]]
    missing_runs: list[str]


def health_view(report: Mapping[str, Any]) -> HealthView:
    causes = list(report.get("causes") or [])
    rows = [{"Código": str(c["code"]), "Severidade": str(c["severity"])} for c in causes]
    notices = [INFO_NOTICES[c["code"]].format(count=(c.get("detail") or {}).get("count", 0))
               for c in causes if c["code"] in INFO_NOTICES]
    facts = report.get("facts")
    if not facts:
        return HealthView(str(report["state"]), rows, notices, "sem dados (banco ou schema indisponível)", None, None,
                          [], [], [])
    live = facts.get("live_runs") or []
    last_cycle = "nenhum ciclo registrado" if not live else (
        f"{live[0]['status']} · market_now {fmt_ts((live[0].get('detail') or {}).get('market_now'))} · "
        f"iniciado {fmt_ts(live[0].get('started_at'))}")
    review_queue = [{"Motivo": str(reason), "Ordens": str(count)}
                    for reason, count in sorted((facts.get("needs_review") or {}).items())]
    incidents = [{
        "Tipo": str(g["kind"]), "Motivo": str(g.get("reason") or EMPTY), "Ocorrências": str(g["occurrences"]),
        "Ordens afetadas": str(g["affected_count"]), "Último": fmt_ts(g.get("last_recorded_at")),
    } for g in facts.get("incident_groups") or []]
    missing = [f"{kind}: {', '.join(days)}" for kind, days in sorted((facts.get("missing_runs") or {}).items())]
    return HealthView(str(report["state"]), rows, notices, last_cycle, int(facts["frozen_orders"]),
                      int(facts["incidents_total"]), review_queue, incidents, missing)


def coverage_rows(overview: Mapping[str, Any]) -> list[dict[str, str]]:
    """M8: the API sums expected/missing bars per order, so the columns are order-minutes, not session minutes."""
    return [{
        "Pregão": str(c["session_date"]), "Ordens": str(c["orders"]),
        "Minutos-ordem esperados": str(c["expected_bars"]), "Minutos-ordem ausentes": str(c["missing_bars"]),
        "Cobertura": EMPTY if c.get("coverage_pct") is None else f"{c['coverage_pct']}%",
    } for c in overview.get("coverage") or []]


def gap_rows(overview: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{
        "Ordem": str(g["order_id"])[:8], "Ticker": str(g["ticker"]), "Início": fmt_ts(g.get("gap_start_ts")),
        "Minutos": str(g["minutes"]), "Gravado": fmt_ts(g.get("recorded_at")),
    } for g in overview.get("data_gaps") or []]


def comparison_rows(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for group in groups:
        s = group["summary"]
        rows.append({
            "Grupo": "(sem valor)" if group.get("key") is None else str(group["key"]), "Trades": str(s["trades"]),
            "Win rate": fmt_pct(s.get("win_rate")), "Expectância": fmt_r(s.get("expectancy_r")),
            "IC expectância": _interval(s.get("expectancy_ci"), fmt_r) or EMPTY,
            "Profit factor": fmt_decimal(s.get("profit_factor")), "Taxa de execução": fmt_pct(s.get("execution_rate")),
            "Excluídas (revisão)": str(s["excluded_needs_review"]["count"]),
        })
    return rows


def _closed_r(order: Mapping[str, Any]) -> Decimal | None:
    return _decimal(order["r_multiple"]) if order.get("status") == "CLOSED" and order.get("r_multiple") is not None \
        else None


def replay_pairs(originals: Sequence[Mapping[str, Any]], replays: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    by_id = {str(o["order_id"]): o for o in originals}
    rows: list[dict[str, str]] = []
    for replay in sorted(replays, key=lambda item: (str(item.get("created_at") or ""), str(item["order_id"]))):
        source = by_id.get(str(replay.get("replay_of_order_id")))
        if source is None:
            continue
        original_r, replay_r = _closed_r(source), _closed_r(replay)
        rows.append({
            "Original": str(source["order_id"]), "Replay": str(replay["order_id"]),
            "Modo": str(replay.get("replay_mode") or EMPTY), "Status original": str(source["status"]),
            "Status replay": str(replay["status"]), "R original": fmt_r(original_r), "R replay": fmt_r(replay_r),
            "Diferença (R)": EMPTY if original_r is None or replay_r is None else fmt_r(replay_r - original_r),
            "Fill model": f"{source['fill_model_version']} → {replay['fill_model_version']}",
        })
    return rows


def outbox_rows(alerts: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "alert_key": str(a["alert_key"]), "Tipo": str(a["kind"]), "Criado": fmt_ts(a.get("created_at")),
        "Falhas": str(a["failures"]), "Último resultado": str(a.get("last_outcome") or "PENDENTE"),
        "HTTP": fmt_price(a.get("last_status_code")), "Erro": str(a.get("last_error_type") or EMPTY),
    } for a in alerts]


def health_log_rows(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{"Estado": str(e["state"]), "Causas": ", ".join(e.get("cause_codes") or []) or EMPTY,
             "Observado": fmt_ts(e.get("observed_at"))} for e in entries]


def rule_rows(rules: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Tipo": str(r["kind"]), "Nível": fmt_price(r.get("level")), "Direção": str(r.get("direction") or EMPTY),
        "Limiar CMF": fmt_price(r.get("cmf_threshold")), "Janela": fmt_price(r.get("window_bars")),
        "Cooldown (min)": str(r["cooldown_minutes"]), "Criada": fmt_ts(r.get("created_at")),
    } for r in rules]


def market_tickers(watchlist: Sequence[Mapping[str, Any]], orders: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({str(item["ticker"]) for item in watchlist} | {str(item["ticker"]) for item in orders})


@dataclass(frozen=True)
class VirtualPortfolioView:
    disclaimer: str
    rows: list[dict[str, str]]
    totals: list[tuple[str, str]]


def virtual_portfolio_view(payload: Mapping[str, Any]) -> VirtualPortfolioView:
    portfolio = payload["portfolio"]
    rows = [{
        "Ticker": str(m["position"]["ticker"]), "Direção": str(m["position"]["direction"]),
        "Estratégia": str(m["position"]["strategy"]), "Origem": str(m["position"]["origin"]),
        "Qtd (por 1R)": fmt_price(m["position"]["qty_open"]), "Entrada média": fmt_price(m["position"]["avg_entry"]),
        "Último fechamento": fmt_price(m["position"].get("last_close")),
        "Candle": fmt_ts(m["position"].get("last_close_ts")), "P&L não realizado": fmt_money(m.get("unrealized_pnl")),
        "R não realizado": fmt_r(m.get("unrealized_r")), "R marcado (aberto)": fmt_r(m.get("open_r")),
        "Alocação": EMPTY if m.get("allocation_pct") is None else f"{fmt_decimal(m['allocation_pct'])}%",
        "Frozen": "sim" if m["position"].get("frozen") else "não",
    } for m in portfolio["positions"]]
    totals = portfolio["totals"]
    return VirtualPortfolioView(str(portfolio["disclaimer"]), rows, [
        ("Posições", str(totals["positions"])), ("Sem marcação", str(totals["unmarked"])),
        ("Notional (por 1R)", fmt_money(totals["notional"])),
        ("P&L não realizado (por 1R)", fmt_money(totals["unrealized_pnl"])),  # M9: sums different risk_amounts
        ("R não realizado", fmt_r(totals["unrealized_r"])),
    ])


def real_portfolio_message(payload: Mapping[str, Any]) -> str | None:
    if payload.get("available"):
        return None
    reason = payload.get("reason")
    if reason == "PHASE_0_PENDING":
        return REAL_PHASE_0_MESSAGE
    reason_text = str(reason) if reason else "motivo não informado"
    error = payload.get("error")
    return f"Portfólio real indisponível ({reason_text}{': ' + str(error) if error else ''})."


def real_portfolio_rows(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"Ticker": str(p["ticker"]), "Quantidade": fmt_price(p["quantity"]),
             "Custo médio": fmt_price(p.get("average_cost")), "Valor de mercado": fmt_money(p.get("market_value")),
             "as_of": fmt_ts(p.get("as_of"))} for p in payload.get("positions") or []]


@dataclass(frozen=True)
class PressureDisplay:
    title: str
    lines: list[str]
    method: str
    disclaimer: str


def pressure_display(payload: Mapping[str, Any]) -> PressureDisplay:
    """D44: method and disclaimer are always present, even when the estimate is unavailable."""
    method = str(payload.get("method") or "método não informado")
    disclaimer = str(payload.get("disclaimer") or "Estimativa derivada de candles; não é fluxo de ordens.")
    if not payload.get("available"):
        return PressureDisplay(PRESSURE_TITLE, [f"Indisponível: {payload.get('reason')}"], method, disclaimer)
    values = payload["values"]
    lines = [
        f"Janela: {values['bars']} candles até {fmt_ts(values['last_bar_ts'])}",
        f"CMF: {values['chaikin_money_flow']}", f"Inclinação do OBV: {values['obv_slope']}",
        f"VWAP da janela: {values['vwap']} (distância {values['vwap_distance_pct']}%)",
        f"CLV do último candle: {values['close_location_value']}",
    ]
    if payload.get("side"):
        lines.append(f"Pressão forte estimada: {payload['side']} (limiar CMF {payload['cmf_threshold']})")
    return PressureDisplay(PRESSURE_TITLE, lines, method, disclaimer)


class InputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _decimal_input(text: str, name: str) -> Decimal:
    try:
        value = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        raise InputError(f"INVALID_DECIMAL:{name}") from None
    if not value.is_finite():
        raise InputError(f"INVALID_DECIMAL:{name}")
    return value


def rule_body(
    *, kind: str, level: str, direction: str, cmf_threshold: str, window_bars: int, cooldown_minutes: int
) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": kind, "cooldown_minutes": cooldown_minutes}
    if kind == "PRICE_CROSS":
        body["level"] = _decimal_input(level, "level")
        body["direction"] = direction
    else:
        body["cmf_threshold"] = _decimal_input(cmf_threshold, "cmf_threshold")
        body["window_bars"] = window_bars
    return body


def watchlist_ticker(text: str) -> str:
    """M7: the server decodes %2F before routing, so a ticker with "/" could never reach /watchlist/{ticker}."""
    ticker = text.strip().upper()
    if not ticker or any(character.isspace() or character == "/" for character in ticker):
        raise InputError("TICKER_INVALID")
    return ticker


def curve_limit_notice(fetched: int) -> str | None:
    """D59: GET /orders returns at most 1000 rows, newest first; a full page means older trades were left out."""
    return CURVE_LIMIT_NOTICE if fetched >= ORDER_LIST_LIMIT else None


OBSERVATION_PRESSURE_TITLE = "Pressão estimada (OHLCV) × resultado — associação descritiva, não fluxo de ordens"
ALIGNMENT_LABELS = {"ALIGNED": "a favor", "OPPOSED": "contra", "NEUTRAL": "neutra", "UNAVAILABLE": "indisponível"}
STRENGTH_LABELS = {"WEAK": "fraca", "MODERATE": "moderada", "STRONG": "forte"}


def fmt_duration(seconds: Any) -> str:
    if seconds is None:
        return EMPTY
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


def _pairs(mapping: Mapping[str, Any] | None) -> str:
    return ", ".join(f"{key}: {value}" for key, value in sorted((mapping or {}).items())) or EMPTY


def pressure_bucket_label(bucket: Mapping[str, Any]) -> str:
    alignment = ALIGNMENT_LABELS.get(str(bucket.get("alignment")), str(bucket.get("alignment")))
    strength = bucket.get("strength")
    return alignment if strength is None else f"{alignment} · {STRENGTH_LABELS.get(str(strength), str(strength))}"


@dataclass(frozen=True)
class ObservationView:
    title: str
    status: str
    cards: list[MetricCard]
    operations: list[dict[str, str]]
    trades: list[dict[str, str]]
    trades_caption: str
    latency: list[dict[str, str]]
    pressure: list[dict[str, str]]
    pressure_method: str
    pressure_disclaimer: str
    pressure_note: str


def _latency_row(label: str, stats: Mapping[str, Any]) -> dict[str, str]:
    return {"Medida": label, "Fills": str(stats["count"]), "Mediana": fmt_duration(stats.get("median_seconds")),
            "p90": fmt_duration(stats.get("p90_seconds")), "Máx": fmt_duration(stats.get("max_seconds"))}


def observation_view(report: Mapping[str, Any]) -> ObservationView:
    failures, quality, action = report["provider_failures"], report["data_quality"], report["actionability"]
    rechecks, alerts, worker, health = report["rechecks"], report["alerts"], report["worker"], report["health"]
    trades, latency, pressure = report["trades"], report["latency"], report["pressure"]
    codes: dict[str, int] = {}
    for item in failures["by_run_kind"]:
        for code, occurrences in item["codes"].items():
            codes[code] = codes.get(code, 0) + int(occurrences)
    failed_attempts = int(alerts["attempts"].get("FAILED", 0))
    coverage = quality.get("coverage_pct")
    seconds = ", ".join(
        f"{state}: {fmt_duration(value)}" for state, value in sorted(health["seconds_by_state"].items())
    )
    return ObservationView(
        title=f"Pregão {report['session_day']}",
        status="Janela completa" if report["complete"] else f"Janela parcial (as-of {fmt_ts(report['as_of'])})",
        cards=[
            MetricCard("Falhas de provider", str(failures["total_failures"]), None),
            MetricCard("Minutos ausentes", f"{quality['missing_bars']}/{quality['expected_bars']}", None),
            MetricCard("503 de actionability", str(action["unverifiable"]), None),
            MetricCard("Linhas de recheck", str(rechecks["rows"]), None),
            MetricCard("Falhas de entrega", str(failed_attempts), None),
            MetricCard("Reinícios do worker", str(worker["restarts"]), None),
            MetricCard("Transições de saúde", str(health["transitions"]), None),
            MetricCard("Trades fechados", str(trades["stats"]["trades"]), None),
            MetricCard("R somado", fmt_r(trades["stats"]["sum_r"]), None),
            MetricCard("Latência mediana", fmt_duration(latency["bar"]["median_seconds"]), None),
        ],
        operations=[
            {"Métrica": "Falhas de provider", "Valor": str(failures["total_failures"]),
             "Detalhe": f"{_pairs(codes)} · maior sequência LIVE: {failures['consecutive_live_max']}"},
            {"Métrica": "Candles ausentes", "Valor": f"{quality['missing_bars']}/{quality['expected_bars']}",
             "Detalhe": f"cobertura {EMPTY if coverage is None else f'{coverage}%'} · DATA_GAP {quality['gaps']} "
                        f"({quality['gap_minutes']} min) · não avaliadas: {_pairs(quality['not_evaluated'])}"},
            {"Métrica": "503 de actionability", "Valor": f"{action['unverifiable']}/{action['requests']}",
             "Detalhe": _pairs(action["unverifiable_causes"])},
            {"Métrica": "DATA_QUALITY_RECHECK", "Valor": str(rechecks["rows"]),
             "Detalhe": f"status: {_pairs(rechecks['statuses'])} · sobre este pregão: "
                        f"{_pairs(rechecks['about_this_session'])}"},
            {"Métrica": "Entrega de alertas", "Valor": f"{failed_attempts} falha(s)",
             "Detalhe": f"tentativas: {_pairs(alerts['attempts'])} · tipos: {_pairs(alerts['failure_types'])} · "
                        f"pendentes no fim: {alerts['pending_at_end']}"},
            {"Métrica": "Worker", "Valor": f"{worker['restarts']} reinício(s)",
             "Detalhe": f"inícios {worker['starts']} · fins sem parada {worker['unclean_ends']} · paradas: "
                        f"{_pairs(worker['stops'])} · sessão aberta no fim: "
                        f"{'sim' if worker['open_session_at_end'] else 'não'}"},
            {"Métrica": "Saúde", "Valor": f"{health['transitions']} transição(ões)",
             "Detalhe": f"{health['state_at_start']} → {health['state_at_end']} · linhas do log {health['log_rows']} · "
                        f"{seconds or EMPTY}"},
        ],
        trades=[
            {"Ordem": str(row["order_id"])[:8], "Ticker": str(row["ticker"]), "Origem": str(row["origin"]),
             "Fechada": fmt_ts(row["closed_at"]), "R": fmt_r(row["r_multiple"]), "MFE": fmt_r(row.get("mfe_r")),
             "MAE": fmt_r(row.get("mae_r")), "Revisão": "sim" if row["needs_review"] else "não",
             "Pressão (estimativa)": pressure_bucket_label({"alignment": row["pressure_alignment"],
                                                            "strength": row.get("pressure_strength")})}
            for row in trades["rows"]
        ],
        trades_caption=(f"Excluídas por revisão: {trades['excluded_needs_review']} · replay fechadas (fora das "
                        f"métricas): {trades['replay_closed']} · sem fill: {_pairs(latency['unfilled'])}"),
        latency=[
            _latency_row("Candle do fill", latency["bar"]), _latency_row("Gravação do fill", latency["recorded"]),
            *(_latency_row(f"Candle · {origin}", stats) for origin, stats in sorted(latency["bar_by_origin"].items())),
        ],
        pressure=[
            {"Pressão": pressure_bucket_label(bucket), "Trades": str(bucket["trades"]), "Wins": str(bucket["wins"]),
             "R somado": fmt_r(bucket["sum_r"]),
             "R médio (n)": f"{fmt_r(bucket.get('mean_r'))} (n={bucket['trades']})"}  # D66: n beside the mean
            for bucket in pressure["buckets"]
        ],
        pressure_method=str(pressure["method"]), pressure_disclaimer=str(pressure["disclaimer"]),
        pressure_note=str(pressure["association_note"]),
    )


def observation_day_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"Pregão": str(day["session_day"]), "Completo": "sim" if day["complete"] else "não",
         "Falhas de provider": str(day["provider_failures"]),
         "Ausentes": f"{day['missing_bars']}/{day['expected_bars']}",
         "503": str(day["actionability_unverifiable"]), "Rechecks": str(day["rechecks"]),
         "Falhas de alerta": str(day["alert_failures"]), "Reinícios": str(day["worker_restarts"]),
         "Fins sem parada": str(day["unclean_worker_ends"]), "Transições": str(day["health_transitions"]),
         "Fills": str(day["fills"]), "Trades": str(day["trades"]), "R": fmt_r(day["sum_r"])}
        for day in summary["days"]
    ]


# --- Research (Plan 5) ------------------------------------------------------------------------------------
RESEARCH_ISOLATION_NOTICE = (
    "Observações de pesquisa: nenhuma delas cria sinal ou ordem. A promoção para sinal em paper é uma decisão "
    "separada e explícita, pelo intake existente.")
RESEARCH_NO_MODEL = "Nenhum modelo aprovado ainda: a coluna de probabilidade fica vazia até existir um."
PATTERN_LABELS = {
    "DOJI": "Doji", "DRAGONFLY_DOJI": "Doji libélula", "GRAVESTONE_DOJI": "Doji lápide", "HAMMER": "Martelo",
    "HANGING_MAN": "Homem enforcado", "INVERTED_HAMMER": "Martelo invertido", "SHOOTING_STAR": "Estrela cadente",
    "BULLISH_ENGULFING": "Engolfo de alta", "BEARISH_ENGULFING": "Engolfo de baixa",
    "PIERCING_LINE": "Linha penetrante", "DARK_CLOUD_COVER": "Nuvem negra", "MORNING_STAR": "Estrela da manhã",
    "EVENING_STAR": "Estrela da tarde", "THREE_WHITE_SOLDIERS": "Três soldados brancos",
    "THREE_BLACK_CROWS": "Três corvos negros",
}
TREND_LABELS = {"UP": "alta", "DOWN": "baixa", "SIDEWAYS": "lateral", "UNKNOWN": EMPTY}
BREAKOUT_LABELS = {"ABOVE_RESISTANCE": "acima da resistência", "BELOW_SUPPORT": "abaixo do suporte",
                   "INSIDE": "dentro da faixa", "UNKNOWN": EMPTY}


def pattern_label(pattern: Any) -> str:
    return PATTERN_LABELS.get(str(pattern), str(pattern))


def fmt_probability(value: Any) -> str:
    """A probability is shown as a percentage, or as EMPTY when no model has scored the candidate."""
    return EMPTY if value is None else f"{_decimal(value) * 100:.1f}%"


def fmt_score(value: Any) -> str:
    return EMPTY if value is None else f"{_decimal(value):.2f}"


def fmt_signed_pct(value: Any) -> str:
    return EMPTY if value is None else f"{_decimal(value):+.2f}%"


def _feature(candidate: Mapping[str, Any], name: str) -> Any:
    return (candidate.get("features") or {}).get(name)


def scanner_rows(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The scanner table of spec 21, in the order the brief lists its columns."""
    return [{
        "Ticker": str(c["ticker"]), "Timeframe": str(c["timeframe"]), "Padrão": pattern_label(c["pattern"]),
        "Direção": str(c["direction"]), "Score do padrão": fmt_score(c.get("deterministic_score")),
        "Tendência": TREND_LABELS.get(str(_feature(c, "short_term_trend")), EMPTY),
        "RSI": fmt_decimal(_feature(c, "rsi14"), 1),
        "Volume relativo": fmt_decimal(_feature(c, "relative_volume")),
        "Pressão (estimativa)": str(_feature(c, "pressure_side") or EMPTY),
        "Distância VWAP": fmt_signed_pct(_feature(c, "vwap_distance_pct")),
        "Distância suporte": fmt_signed_pct(_feature(c, "support_distance_pct")),
        "Detectado em": fmt_ts(c.get("detected_at")),
        "Probabilidade ML": fmt_probability(c.get("ml_probability")),
    } for c in candidates]


def levels_rows(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Proposed levels beside the reason a chain was rejected: an invalid chain is shown, never hidden."""
    return [{
        "Ticker": str(c["ticker"]), "Padrão": pattern_label(c["pattern"]), "Direção": str(c["direction"]),
        "Zona": f"{fmt_price(c.get('entry_zone_low'))}–{fmt_price(c.get('entry_zone_high'))}",
        "Stop": fmt_price(c.get("stop")), "Alvo 1": fmt_price(c.get("target1")),
        "Alvo 2": fmt_price(c.get("target2")), "R:R": fmt_decimal(c.get("risk_reward")),
        "Válido": "sim" if c.get("levels_valid") else "não",
        "Motivos": ", ".join(str(code) for code in (c.get("levels_errors") or [])) or EMPTY,
    } for c in candidates]


def marker_label(marker: Mapping[str, Any]) -> str:
    """Spec 22: the label a detected pattern carries on the chart."""
    return (f"{pattern_label(marker.get('pattern'))} · {fmt_score(marker.get('pattern_score'))} · "
            f"{marker.get('timeframe')}")


@dataclass(frozen=True)
class CandidateView:
    title: str
    direction: str
    detected_at: str
    score: str
    probability: str
    evidence: list[dict[str, str]]
    context: list[dict[str, str]]
    levels: list[dict[str, str]]
    interpretation: str
    pressure_disclaimer: str


def _pairs_table(values: Mapping[str, Any], key_name: str = "Campo") -> list[dict[str, str]]:
    return [{key_name: str(name), "Valor": EMPTY if value is None else str(value)}
            for name, value in sorted(values.items())]


def candidate_view(payload: Mapping[str, Any]) -> CandidateView:
    candidate = payload["candidate"]
    detection = payload.get("detection") or {}
    thesis = candidate.get("thesis_document") or {}
    features = candidate.get("feature_document") or {}
    levels = thesis.get("levels") or {}
    return CandidateView(
        title=f"{candidate['ticker']} · {pattern_label(candidate['pattern'])} · {candidate['timeframe']}",
        direction=str(candidate["direction"]),
        detected_at=fmt_ts(candidate.get("detected_at")),
        score=fmt_score(candidate.get("deterministic_score")),
        probability=fmt_probability(candidate.get("ml_probability")),
        evidence=_pairs_table(detection.get("evidence") or {}, "Evidência"),
        context=[
            {"Métrica": "Tendência curta", "Valor": TREND_LABELS.get(str(features.get("short_term_trend")), EMPTY)},
            {"Métrica": "Tendência média", "Valor": TREND_LABELS.get(str(features.get("medium_term_trend")), EMPTY)},
            {"Métrica": "Rompimento", "Valor": BREAKOUT_LABELS.get(str(features.get("breakout")), EMPTY)},
            {"Métrica": "RSI 14", "Valor": fmt_decimal(features.get("rsi14"), 1)},
            {"Métrica": "ATR 14", "Valor": fmt_price(features.get("atr14"))},
            {"Métrica": "Volume relativo", "Valor": fmt_decimal(features.get("relative_volume"))},
            {"Métrica": "CMF (estimativa)", "Valor": fmt_price(features.get("cmf"))},
            {"Métrica": "Distância VWAP", "Valor": fmt_signed_pct(features.get("vwap_distance_pct"))},
            {"Métrica": "Distância suporte", "Valor": fmt_signed_pct(features.get("support_distance_pct"))},
            {"Métrica": "Distância resistência",
             "Valor": fmt_signed_pct(features.get("resistance_distance_pct"))},
        ],
        levels=[] if not levels else [{
            "Zona": f"{fmt_price(levels.get('entry_zone_low'))}–{fmt_price(levels.get('entry_zone_high'))}",
            "Stop": fmt_price(levels.get("stop")), "Alvo 1": fmt_price(levels.get("target1")),
            "Alvo 2": fmt_price(levels.get("target2")), "R:R": fmt_decimal(levels.get("risk_reward")),
            "Válido": "sim" if levels.get("valid") else "não",
            "Motivos": ", ".join(str(code) for code in (levels.get("errors") or [])) or EMPTY,
        }],
        interpretation=str(payload.get("score_interpretation") or EMPTY),
        pressure_disclaimer=str(features.get("pressure_disclaimer") or ""),
    )


def backtest_rows(backtests: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Every rate beside the sample it came from: no percentage is ever shown without its count (spec 21)."""
    return [{
        "Padrão": pattern_label(b["pattern"]), "Timeframe": str(b["timeframe"]),
        "Universo": str(b.get("ticker") or "todos"), "Split": str(b["split"]),
        "Amostras": str(b["samples"]), "Resolvidos": str(b["resolved"]),
        "Wins": str(b["wins"]), "Losses": str(b["losses"]), "Timeouts": str(b["timeouts"]),
        "Win rate (n)": f"{fmt_pct(b.get('win_rate'))} (n={b['resolved']})",
        "Expectância (R)": fmt_r(b.get("expectancy_r")), "Profit factor": fmt_decimal(b.get("profit_factor")),
        "Drawdown máx. (R)": fmt_decimal(b.get("max_drawdown_r")), "MFE": fmt_r(b.get("avg_mfe_r")),
        "MAE": fmt_r(b.get("avg_mae_r")), "Ambíguos": str(b["ambiguous"]),
        "Período": f"{fmt_ts(b.get('period_from'))} → {fmt_ts(b.get('period_to'))}",
    } for b in backtests]


def research_run_rows(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Tipo": str(r["kind"]), "Status": str(r["status"]), "Início": fmt_ts(r.get("started_at")),
        "Fim": fmt_ts(r.get("completed_at")), "data_as_of": fmt_ts(r.get("data_as_of")),
        "Motor": str(r.get("engine_version") or EMPTY),
        "Detecções": str((r.get("detail") or {}).get("detections", EMPTY)),
        "Candidatos": str((r.get("detail") or {}).get("candidates", EMPTY)),
        "Falhas": str(len((r.get("detail") or {}).get("failures", {}) or {})),
    } for r in runs]


def model_rows(models: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{
        "Modelo": str(m["model_name"]), "Versão": str(m["model_version"]), "Tipo": str(m["model_kind"]),
        "Features": str(m["feature_version"]), "Labels": str(m["label_version"]),
        "Treino": f"{fmt_ts(m.get('training_from'))} → {fmt_ts(m.get('training_to'))}",
        "Linhas": str(m["training_rows"]),
        "AUC (validação)": fmt_decimal((m.get("validation_metrics") or {}).get("auc"), 3),
        "Brier (validação)": fmt_decimal((m.get("validation_metrics") or {}).get("brier"), 4),
        "Registrado": fmt_ts(m.get("created_at")),
    } for m in models]
