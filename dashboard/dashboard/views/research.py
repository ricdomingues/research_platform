"""The Research page (Plan 5, D93). Read-only, over HTTP, and never a place where a signal is created.

The page shows what the scan already recorded: the scanner, one asset's chart with its detected patterns, and
the historical statistics behind a pattern. Every percentage is displayed beside the sample it came from, and
the deterministic score always travels with the sentence explaining what it is not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from dashboard.charts import add_pattern_markers, candlestick_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    RESEARCH_ISOLATION_NOTICE,
    RESEARCH_NO_MODEL,
    backtest_rows,
    candidate_view,
    levels_rows,
    market_day_window,
    model_rows,
    pattern_label,
    research_run_rows,
    scanner_rows,
    today_et,
)
from dashboard.views.common import guarded

ALL = "(todos)"
FALLBACK_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]
SCANNER_LIMIT = 200


def _options(values: list[str]) -> list[str]:
    return [ALL, *values]


def _chosen(value: object) -> str | None:
    return None if value in (None, ALL) else str(value)


def render(client: ApiClient) -> None:
    st.title("Pesquisa")
    st.caption(RESEARCH_ISOLATION_NOTICE)
    versions = guarded(client.research_versions) or {}
    patterns = [str(item) for item in versions.get("supported_patterns", [])]
    timeframes = [str(item) for item in versions.get("supported_timeframes", FALLBACK_TIMEFRAMES)]
    if versions:
        st.caption(f"Motor {versions.get('engine_version')} · features {versions.get('feature_version')} · "
                   f"score {versions.get('scoring_version')} · labels {versions.get('label_version')} "
                   f"({versions.get('ambiguity_policy')})")

    st.subheader("Scanner")
    ticker_column, timeframe_column, pattern_column, score_column = st.columns(4)
    ticker = ticker_column.text_input("Ticker", key="research-ticker")
    timeframe = timeframe_column.selectbox("Timeframe", _options(timeframes), key="research-timeframe")
    pattern = pattern_column.selectbox("Padrão", _options(patterns), format_func=lambda name: (
        name if name == ALL else pattern_label(name)), key="research-pattern")
    min_score = score_column.slider("Score mínimo", 0.0, 1.0, 0.0, 0.05, key="research-min-score")
    payload = guarded(lambda: client.research_candidates(
        ticker=ticker.strip().upper() or None, timeframe=_chosen(timeframe), pattern=_chosen(pattern),
        min_score=Decimal(str(min_score)) if min_score else None, limit=SCANNER_LIMIT,
    ))
    candidates = [] if payload is None else list(payload.get("candidates", []))
    if payload is not None:
        if candidates:
            st.dataframe(scanner_rows(candidates), hide_index=True)
        else:
            st.info("Nenhum candidato de pesquisa com esses filtros.")
        st.caption(str(payload.get("score_interpretation", "")))
        if all(item.get("ml_probability") is None for item in candidates):
            st.caption(RESEARCH_NO_MODEL)

    if candidates:
        st.subheader("Níveis propostos")
        st.dataframe(levels_rows(candidates), hide_index=True)
        st.caption("Uma cadeia de níveis inválida é mostrada com o motivo: ela nunca vira sinal.")
        by_id = {str(item["id"]): item for item in candidates}
        selected = st.selectbox(
            "Detalhe do candidato", list(by_id), key="research-candidate",
            format_func=lambda key: (f"{by_id[key]['ticker']} · {pattern_label(by_id[key]['pattern'])} · "
                                     f"{by_id[key]['timeframe']}"),
        )
        detail = None if selected is None else guarded(lambda: client.research_candidate(int(str(selected))))
        if detail is not None:
            view = candidate_view(detail)
            st.markdown(f"**{view.title}** · {view.direction} · detectado {view.detected_at}")
            st.markdown(f"Score determinístico **{view.score}** · probabilidade ML **{view.probability}**")
            columns = st.columns(2)
            with columns[0]:
                st.markdown("**Evidência do padrão**")
                st.dataframe(view.evidence, hide_index=True)
            with columns[1]:
                st.markdown("**Contexto**")
                st.dataframe(view.context, hide_index=True)
            if view.levels:
                st.dataframe(view.levels, hide_index=True)
            st.caption(view.interpretation)
            if view.pressure_disclaimer:
                st.caption(view.pressure_disclaimer)

    st.subheader("Análise do ativo")
    asset_column, asset_timeframe_column, day_column = st.columns(3)
    asset = asset_column.text_input("Ticker do gráfico", key="research-asset")
    asset_timeframe = asset_timeframe_column.selectbox("Timeframe do gráfico", timeframes,
                                                       key="research-asset-timeframe")
    picked = day_column.date_input("Pregão (ET)", value=today_et(), key="research-day")
    symbol = asset.strip().upper()
    if symbol and isinstance(picked, date):
        start, end = market_day_window(picked)
        bars = guarded(lambda: client.market_bars(symbol, start, end))
        markers = guarded(lambda: client.research_markers(symbol, str(asset_timeframe), start, end)) or []
        if bars is not None:
            if bars["bars"]:
                figure = candlestick_figure(bars["bars"], bars["vwap"],
                                            title=f"{symbol} · {picked.isoformat()} · {asset_timeframe}")
                st.plotly_chart(add_pattern_markers(figure, markers))
            else:
                st.info("Nenhum candle armazenado neste pregão.")
            st.caption(f"Candles armazenados lidos as-of {bars['data_as_of']}; "
                       f"{len(markers)} padrão(ões) marcado(s) em {asset_timeframe}.")
    else:
        st.caption("Informe um ticker para ver o gráfico com os padrões detectados.")

    st.subheader("Backtest")
    backtest_pattern_column, backtest_timeframe_column, split_column = st.columns(3)
    backtest_pattern = backtest_pattern_column.selectbox(
        "Padrão avaliado", _options(patterns), key="research-backtest-pattern",
        format_func=lambda name: name if name == ALL else pattern_label(name))
    backtest_timeframe = backtest_timeframe_column.selectbox("Timeframe avaliado", _options(timeframes),
                                                             key="research-backtest-timeframe")
    split = split_column.selectbox("Janela", [ALL, "ALL", "TRAIN", "VALIDATION", "TEST", "FOLD"],
                                   key="research-split")
    backtests = guarded(lambda: client.research_backtests(
        pattern=_chosen(backtest_pattern), timeframe=_chosen(backtest_timeframe), split=_chosen(split),
    ))
    if backtests is not None:
        if backtests:
            st.dataframe(backtest_rows(backtests), hide_index=True)
            st.caption("Toda taxa aparece ao lado da amostra que a produziu; janelas de treino, validação e "
                       "teste são cronológicas, com purga e embargo.")
        else:
            st.info("Nenhum backtest registrado ainda.")

    st.subheader("Modelos e execuções")
    models = guarded(lambda: client.research_models())
    if models is not None:
        if models:
            st.dataframe(model_rows(models), hide_index=True)
        else:
            st.caption(RESEARCH_NO_MODEL)
    runs = guarded(lambda: client.research_runs())
    if runs:
        st.dataframe(research_run_rows(runs), hide_index=True)
