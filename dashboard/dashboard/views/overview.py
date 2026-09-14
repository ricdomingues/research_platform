from __future__ import annotations

import streamlit as st

from dashboard.charts import cumulative_r_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    ORDER_LIST_LIMIT,
    cumulative_r,
    curve_limit_notice,
    metric_cards,
    review_exclusion_text,
    summary_warnings,
)
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Visão geral")
    include = st.toggle("Incluir ordens em revisão", value=False, key="overview-include-review")
    metrics = guarded(lambda: client.metrics(include_needs_review=include))
    closed = guarded(lambda: client.orders(status="CLOSED", limit=ORDER_LIST_LIMIT))
    if metrics is None or closed is None:
        return
    if not metrics["groups"]:
        st.info("Sem métricas.")
        return
    summary = metrics["groups"][0]["summary"]
    for warning in summary_warnings(summary):
        st.warning(warning)
    st.caption(review_exclusion_text(summary))
    columns = st.columns(3)
    for index, card in enumerate(metric_cards(summary)):
        with columns[index % 3]:
            st.metric(card.label, card.value, help=card.interval)
    points = cumulative_r(closed, include_needs_review=include)
    notice = curve_limit_notice(len(closed))
    if notice is not None:
        st.caption(notice)  # D59: a full page of GET /orders drops the oldest closed trades
    if points:
        st.plotly_chart(cumulative_r_figure(points))
    else:
        st.info("Nenhuma ordem fechada ainda.")
