"""The Terminal page (D94): one asset's chart, its detected patterns, and the one way to trade one on paper.

This is the page the owner asked for on 2026-09-22: a chart that behaves like a trading terminal, with the
detected patterns on it and the candidate's own entry, stop and targets drawn as lines, so a setup can be read
where it happened instead of in a table.

Promotion lives here, but the decision does not. The page never judges a candidate: it POSTs to
`/research/candidates/{id}/promote` and shows whatever the engine's boundary answered. When the policy refuses,
every reason is displayed; the override checkbox re-sends the same request with `override=true`, which the
engine records under a distinct `source` so a forced promotion is never mistaken for one the policy allowed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

from dashboard.charts import lightweight_charts
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    TIMEFRAME_MINUTES,
    aggregate_bars,
    describe_api_error,
    fmt_price,
    fmt_ts,
    market_day_window,
    promotion_blockers,
    today_et,
)
from dashboard.views.common import guarded

LOOKBACK_DAYS = 5


def _symbols(client: ApiClient) -> list[str]:
    rows = guarded(client.watchlist) or []
    return [str(row["ticker"]) for row in rows]


def _candidate_label(row: dict[str, Any]) -> str:
    state = "válido" if row.get("levels_valid") else "sem cadeia válida"
    return (f"#{row['id']} {row['pattern']} {row['direction']} "
            f"· score {fmt_price(row['deterministic_score'])} · {state} · {fmt_ts(row['detected_at'])}")


def _render_promotion(client: ApiClient, candidate: dict[str, Any]) -> None:
    """The promotion boundary, shown as the engine answers it. This page decides nothing on its own."""
    st.subheader("Promover para sinal em papel")
    st.caption("A política do motor decide. Esta página apenas envia o pedido e mostra a resposta.")
    if not candidate.get("levels_valid"):
        st.warning("Candidato sem cadeia de níveis válida: não há preços para negociar, nem com override.")
        return
    override = st.checkbox(
        "Forçar promoção (override da política)", key=f"override_{candidate['id']}",
        help="Registra o sinal com source=manual_promotion e grava no thesis tudo o que foi ignorado.",
    )
    auto_order = st.checkbox("Criar ordem automaticamente", value=True, key=f"auto_{candidate['id']}")
    if not st.button("Promover", key=f"promote_{candidate['id']}", type="primary"):
        return
    try:
        result = client.promote_candidate(int(candidate["id"]), override=override, auto_order=auto_order)
    except Exception as exc:  # noqa: BLE001 - the screen shows codes and reasons, never a traceback (D40)
        blockers = promotion_blockers(getattr(exc, "detail", None))
        if blockers:
            st.error("A política recusou a promoção:")
            for code in blockers:
                st.write(f"- `{code}`")
            st.caption("Marque o override acima para promover mesmo assim, por sua conta.")
        else:
            st.error(describe_api_error(exc))
        return
    if result.get("overridden"):
        st.warning(f"Promovido com override. Sinal {result['signal_id']} gravado como promoção manual.")
    else:
        st.success(f"Promovido pela política. Sinal {result['signal_id']}.")
    if result.get("auto_order_id"):
        st.write(f"Ordem criada: `{result['auto_order_id']}`")


def render(client: ApiClient) -> None:
    st.title("Terminal")
    symbols = _symbols(client)
    if not symbols:
        st.info("Watchlist vazia: adicione um ticker para ver o gráfico.")
        return
    columns = st.columns([2, 2, 3])
    symbol = columns[0].selectbox("Ativo", symbols, key="terminal_ticker")
    timeframe = columns[1].selectbox("Timeframe", list(TIMEFRAME_MINUTES), index=1, key="terminal_timeframe")
    days = columns[2].slider("Sessões", 1, LOOKBACK_DAYS, 2, key="terminal_days")

    end_day = today_et()
    start, _ = market_day_window(end_day - timedelta(days=days - 1))
    _, end = market_day_window(end_day)
    bars = guarded(lambda: client.market_bars(str(symbol), start, end))
    if bars is None:
        return
    if not bars["bars"]:
        st.info("Nenhum candle armazenado nesta janela.")
        return
    candles = aggregate_bars(bars["bars"], TIMEFRAME_MINUTES[str(timeframe)])
    markers = guarded(lambda: client.research_markers(str(symbol), str(timeframe), start, end)) or []
    envelope = guarded(lambda: client.research_candidates(
        ticker=str(symbol), timeframe=str(timeframe), limit=50)) or {}
    candidates: list[dict[str, Any]] = [dict(row) for row in envelope.get("candidates", [])]

    chosen: dict[str, Any] | None = None
    if candidates:
        labels = {_candidate_label(row): row for row in candidates}
        picked = str(st.selectbox("Setup detectado", ["(nenhum)", *labels], key="terminal_candidate"))
        chosen = labels.get(picked)
        # Spec 18: the number never appears without the sentence saying what it is not.
        if chosen is not None and envelope.get("score_interpretation"):
            st.caption(str(envelope["score_interpretation"]))

    last = candles[-1]
    price_row = st.columns(4)
    price_row[0].metric("Último", fmt_price(last["close"]))
    price_row[1].metric("Máxima", fmt_price(last["high"]))
    price_row[2].metric("Mínima", fmt_price(last["low"]))
    price_row[3].metric("Candles", str(len(candles)))

    renderLightweightCharts(
        lightweight_charts(candles, markers=markers, levels=chosen), key=f"terminal_{symbol}_{timeframe}"
    )
    st.caption(f"Candles armazenados lidos as-of {bars['data_as_of']}; {len(markers)} padrões detectados na "
               "janela. Gráfico por TradingView Lightweight Charts.")

    if chosen is not None:
        _render_promotion(client, chosen)
