from __future__ import annotations

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import coverage_rows, fmt_ts, gap_rows, health_log_rows, health_view, outbox_rows
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Saúde")
    report = guarded(client.health)
    if report is not None:
        view = health_view(report)
        if view.state == "HEALTHY":
            st.success(f"Estado: {view.state}")
        elif view.state == "DEGRADED":
            st.warning(f"Estado: {view.state}")
        else:
            st.error(f"Estado: {view.state}")
        for notice in view.info_notices:
            st.info(notice)
        st.subheader("Causas")
        st.dataframe(view.causes, hide_index=True)
        st.subheader("Último ciclo")
        st.write(view.last_cycle)
        if view.frozen_orders is not None:
            frozen_col, incidents_col = st.columns(2)
            frozen_col.metric("Ordens frozen", str(view.frozen_orders))
            incidents_col.metric("Incidentes de integridade (total)", str(view.incidents_total))
        for missing in view.missing_runs:
            st.warning(f"Job ausente — {missing}")
        st.subheader("Fila NEEDS_REVIEW por motivo")
        st.dataframe(view.review_queue, hide_index=True)
        st.subheader("Incidentes de integridade (24 h)")
        st.dataframe(view.incidents, hide_index=True)
    overview = guarded(client.quality_overview)
    if overview is not None:
        st.subheader(f"Cobertura de dados em minutos-ordem (últimos {overview['window_days']} dias)")
        st.caption("Somada por ordem: duas ordens do mesmo ticker contam o mesmo minuto duas vezes (M8).")
        st.dataframe(coverage_rows(overview), hide_index=True)
        st.subheader("DATA_GAP")
        st.dataframe(gap_rows(overview), hide_index=True)
    expired = guarded(lambda: client.alert_outbox(outcome="EXPIRED"))
    if expired is not None:
        st.subheader("Alertas EXPIRED")
        st.dataframe(outbox_rows(expired), hide_index=True)
    log = guarded(lambda: client.health_log(limit=20))
    if log:
        last = log[0]
        causes = ", ".join(last.get("cause_codes") or []) or "sem causas"
        st.markdown(f"**Último health_state_log:** {last['state']} · {causes} · {fmt_ts(last.get('observed_at'))}")
        st.dataframe(health_log_rows(log), hide_index=True)
