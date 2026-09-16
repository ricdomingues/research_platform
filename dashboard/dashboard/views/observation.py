from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from dashboard.charts import daily_r_figure, pressure_buckets_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import OBSERVATION_PRESSURE_TITLE, observation_day_rows, observation_view, today_et
from dashboard.views.common import guarded

SUMMARY_DAYS_DEFAULT = 14
MAX_SUMMARY_DAYS = 45  # the API's limit (Plan 4, D63)


def render(client: ApiClient) -> None:
    st.title("Observação")
    st.caption("Relatório só leitura dos dados gravados, as-of o instante do pedido; nenhuma chamada a provider.")
    picked = st.date_input("Pregão (ET)", value=today_et(), key="observation-day")
    if not isinstance(picked, date):
        return
    day: date = picked
    report = guarded(lambda: client.observation_report(day))
    if report is not None:
        view = observation_view(report)
        st.subheader(view.title)
        st.caption(view.status)
        columns = st.columns(5)
        for index, card in enumerate(view.cards):
            with columns[index % 5]:
                st.metric(card.label, card.value)
        st.subheader("Operação")
        st.dataframe(view.operations, hide_index=True)
        st.subheader("Trades virtuais (MFE / MAE)")
        st.caption(view.trades_caption)
        if view.trades:
            st.dataframe(view.trades, hide_index=True)
        else:
            st.info("Nenhum trade fechado nesta janela.")
        st.subheader("Latência sinal → fill")
        st.dataframe(view.latency, hide_index=True)
        st.subheader(OBSERVATION_PRESSURE_TITLE)
        if view.pressure:
            st.dataframe(view.pressure, hide_index=True)
            st.plotly_chart(pressure_buckets_figure(report["pressure"]["buckets"], method=view.pressure_method))
        st.caption(f"Método: {view.pressure_method}")
        st.caption(view.pressure_disclaimer)
        st.caption(view.pressure_note)
    span = st.number_input("Dias corridos no resumo", min_value=1, max_value=MAX_SUMMARY_DAYS,
                           value=SUMMARY_DAYS_DEFAULT, key="observation-span")
    summary = guarded(lambda: client.observation_summary(day - timedelta(days=int(span) - 1), day))
    if summary is not None:
        st.subheader(f"Resumo: {summary['sessions']} pregão(ões)")
        st.dataframe(observation_day_rows(summary), hide_index=True)
        st.plotly_chart(daily_r_figure(summary["days"]))
