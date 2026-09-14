from __future__ import annotations

import streamlit as st

from dashboard.charts import candlestick_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    ORDER_LIST_LIMIT,
    SHORT_NOTICE,
    WINDOW_TRUNCATED_NOTICE,
    event_log_rows,
    order_label,
    order_rows,
    quality_view,
)
from dashboard.views.common import guarded

STATUSES = ["PENDING", "OPEN", "PARTIAL", "CLOSED", "EXPIRED", "INVALIDATED", "CANCELED"]
ALL = "(todos)"
REVIEW = {"em revisão": True, "sem revisão": False}


def render(client: ApiClient) -> None:
    st.title("Ordens")
    status_col, origin_col, strategy_col, review_col = st.columns(4)
    status = status_col.selectbox("Status", [ALL, *STATUSES], key="orders-status")
    origin = origin_col.selectbox("Origem", [ALL, "AUTO_STRATEGY", "MANUAL_USER"], key="orders-origin")
    strategy = strategy_col.text_input("Estratégia", key="orders-strategy")
    review = review_col.selectbox("Revisão", [ALL, *REVIEW], key="orders-review")
    replay = st.checkbox("Mostrar replays", key="orders-replay")
    rows = guarded(lambda: client.orders(
        status=None if status == ALL else status, origin=None if origin == ALL else origin,
        strategy=strategy.strip() or None, replay=replay, needs_review=REVIEW.get(str(review)), limit=ORDER_LIST_LIMIT,
    ))
    if rows is None:
        return
    st.dataframe(order_rows(rows), hide_index=True)
    if not rows:
        return
    by_id = {str(row["order_id"]): row for row in rows}
    selected = st.selectbox("Detalhe da ordem", list(by_id), format_func=lambda oid: order_label(by_id[oid]),
                            key="orders-detail")
    if selected is None:
        return
    order_id = str(selected)
    detail = guarded(lambda: client.order_detail(order_id))
    chart = guarded(lambda: client.order_chart(order_id))
    if detail is None or chart is None:
        return
    if chart["direction"] == "SHORT":
        st.warning(SHORT_NOTICE)
    bars = guarded(lambda: client.market_bars(chart["ticker"], chart["window"]["from"], chart["window"]["to"],
                                              source=chart["price_source"]))
    if bars is not None:
        st.plotly_chart(candlestick_figure(
            bars["bars"], bars["vwap"], title=f"{chart['ticker']} · {chart['status']}", levels=chart["levels"],
            markers=chart["markers"], evaluation_start_ts=chart["evaluation_start_ts"],
        ))
        st.caption(f"Candles armazenados lidos as-of {bars['data_as_of']} (fonte {bars['price_source']}); "
                   f"evaluation_start_ts {chart['evaluation_start_ts']}.")
        if chart.get("window_truncated"):
            st.caption(WINDOW_TRUNCATED_NOTICE)  # M10: later markers and bars fall outside the 7-day window
    quality = quality_view(detail)
    st.subheader("Qualidade de dados")
    st.write(f"Minutos esperados: {quality.expected_bars} · ausentes: {quality.missing_bars} · "
             f"cobertura: {quality.coverage}")
    if quality.events:
        st.dataframe(quality.events, hide_index=True)
    st.markdown("**Rechecks (DATA_QUALITY_RECHECK)**")
    if quality.rechecks:
        st.dataframe(quality.rechecks, hide_index=True)
    else:
        st.caption("Nenhum recheck.")
    st.subheader("Log de eventos")
    st.dataframe(event_log_rows(detail["events"]), hide_index=True)
