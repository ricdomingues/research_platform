"""Plain-text renderings of the observation report for the CLI (Plan 4, D70). Counts, codes, ids and labels only."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from core.domain.hashing import canonical_json
from virtual_orders.analytics.observation import LatencyStats, PressureBucket, TradeStats
from virtual_orders.readmodels.observation import ObservationReport, ObservationSummary

EMPTY = "—"
BUCKET_HEADERS = ("Alinhamento", "Força", "Trades", "Wins", "R somado", "R médio (n)")


def render_json(document: ObservationReport | ObservationSummary) -> str:
    return json.dumps(json.loads(canonical_json(asdict(document))), indent=2, sort_keys=True, ensure_ascii=False)


def _cell(value: Any) -> str:
    if value is None:
        return EMPTY
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return ", ".join(f"{key}: {item}" for key, item in sorted(value.items())) or EMPTY
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value) or EMPTY
    return str(value)


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return [*lines, ""]


def _stats(stats: TradeStats) -> list[str]:
    return _table(
        ("Trades", "Wins", "Losses", "Win rate", "R somado", "R médio", "MFE médio", "MFE mediano", "MAE médio",
         "MAE mediano"),
        [(stats.trades, stats.wins, stats.losses, stats.win_rate, stats.sum_r, stats.mean_r, stats.mean_mfe_r,
          stats.median_mfe_r, stats.mean_mae_r, stats.median_mae_r)],
    )


def _latency(label: str, stats: LatencyStats) -> tuple[str, int, int | None, int | None, int | None]:
    return (label, stats.count, stats.median_seconds, stats.p90_seconds, stats.max_seconds)


def _buckets(buckets: Iterable[PressureBucket]) -> list[str]:
    """D66: n always beside the mean; the mean is empty below the minimum number of trades."""
    return _table(BUCKET_HEADERS, [
        (bucket.alignment, bucket.strength, bucket.trades, bucket.wins, bucket.sum_r,
         f"{_cell(bucket.mean_r)} (n={bucket.trades})")
        for bucket in buckets
    ])


def _report(report: ObservationReport) -> list[str]:
    failures, quality, action = report.provider_failures, report.data_quality, report.actionability
    rechecks, alerts, worker, health = report.rechecks, report.alerts, report.worker, report.health
    trades, latency, pressure = report.trades, report.latency, report.pressure
    lines = [
        f"# Observação em paper — pregão {report.session_day.isoformat()}", "",
        f"- Janela: {report.window_start.isoformat()} → {report.window_end.isoformat()}",
        f"- As-of: {report.as_of.isoformat()} ({'completo' if report.complete else 'parcial'})",
        f"- Versão: {report.report_version}", "",
        "## Falhas de provider", "",
        f"Total: {failures.total_failures} · maior sequência de ciclos LIVE com o mesmo feed falhando: "
        f"{failures.consecutive_live_max}", "",
        *_table(("Run", "Runs", "Runs FAILED", "Erros", "Runs com falha", "Falhas", "Códigos", "Feeds"),
                [(item.run_kind, item.runs, item.failed_runs, item.failed_run_errors, item.runs_with_failures,
                  item.failures, item.codes, item.feeds) for item in failures.by_run_kind]),
        "## Candles ausentes", "",
        *_table(("Ordens medidas", "Minutos esperados", "Ausentes", "Cobertura %", "Ordens com ausência", "DATA_GAP",
                 "Minutos em gap", "Não avaliadas"),
                [(quality.orders_measured, quality.expected_bars, quality.missing_bars, quality.coverage_pct,
                  quality.orders_with_missing, quality.gaps, quality.gap_minutes, quality.not_evaluated)]),
        "## Actionability (503)", "",
        *_table(("Pedidos", "Resultados", "503", "Causas do 503", "Taxa de 503", "Sem resposta"),
                [(action.requests, action.results, action.unverifiable, action.unverifiable_causes,
                  action.unverifiable_rate, action.unfinished)]),
        "## DATA_QUALITY_RECHECK", "",
        *_table(("Runs", "Linhas", "Status", "Motivos terminais", "Pregões", "Sobre este pregão", "Não avaliadas",
                 "Ignoradas (já tratadas)"),
                [(rechecks.runs, rechecks.rows, rechecks.statuses, rechecks.terminal_reasons,
                  [day.isoformat() for day in rechecks.sessions], rechecks.about_this_session,
                  rechecks.not_evaluated, rechecks.skipped)]),
        "## Entrega de alertas", "",
        *_table(("Criados", "Tentativas", "Alertas com falha", "Tipos de falha", "Pendentes no fim"),
                [(alerts.created, alerts.attempts, alerts.failed_alerts, alerts.failure_types, alerts.pending_at_end)]),
        "## Reinícios do worker", "",
        *_table(("Inícios", "Reinícios", "Fins sem parada", "Paradas", "Códigos de saída", "Versões",
                 "Sessão aberta no fim"),
                [(worker.starts, worker.restarts, worker.unclean_ends, worker.stops, worker.exit_codes,
                  worker.code_versions, "sim" if worker.open_session_at_end else "não")]),
        "Sessão aberta = sem linha de parada: rodando, ou terminou sem parada (só se sabe no próximo início).", "",
        "## Transições de saúde", "",
        *_table(("No início", "Transições", "Linhas do log", "Entradas por estado", "Causas", "Segundos por estado",
                 "No fim"),
                [(health.state_at_start, health.transitions, health.log_rows, health.entered, health.cause_codes,
                  health.seconds_by_state, health.state_at_end)]),
        "## Trades virtuais", "",
        f"Criadas: {_cell(trades.created)} · fills: {trades.filled} · fechadas: {trades.closed} · excluídas por "
        f"revisão: {trades.excluded_needs_review} · replay fechadas (fora das métricas): {trades.replay_closed}", "",
        *_stats(trades.stats),
        "### MFE / MAE por trade", "",
        *_table(("Ordem", "Ticker", "Direção", "Origem", "Fechada", "R", "MFE", "MAE", "Revisão"),
                [(row.order_id, row.ticker, row.direction, row.origin, row.closed_at, row.r_multiple, row.mfe_r,
                  row.mae_r, "sim" if row.needs_review else "não") for row in trades.rows]),
        "## Latência sinal → fill", "",
        *_table(("Medida", "Fills", "Mediana (s)", "p90 (s)", "Máx (s)"),
                [_latency("candle do fill", latency.bar), _latency("gravação do fill", latency.recorded),
                 *(_latency(f"candle · {origin}", stats) for origin, stats in latency.bar_by_origin.items())]),
        f"Sem fill (criadas na janela): {_cell(latency.unfilled)}", "",
        "## Pressão estimada × resultado", "",
        f"- Estimativa: {pressure.method} · últimos {pressure.window_bars} candles antes do sinal (atravessando "
        f"pregões) · limiar CMF {pressure.cmf_threshold} · forte a partir de {pressure.strong_cmf} · média só com "
        f"n ≥ {pressure.min_trades_for_mean}",
        f"- {pressure.disclaimer}",
        f"- {pressure.association_note}", "",
        *_buckets(pressure.buckets),
        f"Sem estimativa: {_cell(pressure.unavailable_reasons)}", "",
        "## Definições", "",
        *(f"- **{name}**: {text}" for name, text in report.definitions.items()),
    ]
    return lines


def _summary(summary: ObservationSummary) -> list[str]:
    pressure = summary.pressure
    return [
        f"# Observação em paper — resumo {summary.first_day.isoformat()} a {summary.last_day.isoformat()}", "",
        f"- As-of: {summary.as_of.isoformat()} · pregões: {summary.sessions} · versão: {summary.report_version}", "",
        "## Por pregão", "",
        *_table(("Pregão", "Completo", "Falhas de provider", "Minutos ausentes", "503", "Rechecks", "Falhas de alerta",
                 "Alertas expirados", "Reinícios", "Fins sem parada", "Transições", "Fills", "Trades", "R somado"),
                [(row.session_day.isoformat(), "sim" if row.complete else "não", row.provider_failures,
                  f"{row.missing_bars}/{row.expected_bars}", row.actionability_unverifiable, row.rechecks,
                  row.alert_failures, row.alerts_expired, row.worker_restarts, row.unclean_worker_ends,
                  row.health_transitions, row.fills, row.trades, row.sum_r) for row in summary.days]),
        "## Trades (sem revisão)", "", f"Excluídas por revisão: {summary.excluded_needs_review}", "",
        *_stats(summary.trades),
        "## Latência sinal → fill", "",
        *_table(("Medida", "Fills", "Mediana (s)", "p90 (s)", "Máx (s)"),
                [_latency("candle do fill", summary.latency), _latency("gravação do fill", summary.recorded_latency)]),
        "## Pressão estimada × resultado", "",
        f"- Estimativa: {pressure.method}", f"- {pressure.disclaimer}", f"- {pressure.association_note}", "",
        *_buckets(pressure.buckets),
        f"Sem estimativa: {_cell(pressure.unavailable_reasons)}",
    ]


def render_markdown(document: ObservationReport | ObservationSummary) -> str:
    lines = _summary(document) if isinstance(document, ObservationSummary) else _report(document)
    return "\n".join(lines) + "\n"
