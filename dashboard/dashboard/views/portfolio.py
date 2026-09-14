from __future__ import annotations

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import real_portfolio_message, real_portfolio_rows, virtual_portfolio_view
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Portfólio")
    st.subheader("Portfólio virtual (por 1R normalizado)")
    virtual = guarded(client.virtual_portfolio)
    if virtual is not None:
        view = virtual_portfolio_view(virtual)
        st.caption(view.disclaimer)
        columns = st.columns(len(view.totals))
        for column, (label, value) in zip(columns, view.totals, strict=True):
            column.metric(label, value)
        if view.rows:
            st.dataframe(view.rows, hide_index=True)
        else:
            st.info("Nenhuma posição virtual aberta.")
    st.subheader("Portfólio real (somente leitura)")
    real = guarded(client.real_portfolio)
    if real is not None:
        message = real_portfolio_message(real)
        if message is not None:
            st.info(message)
        else:
            st.dataframe(real_portfolio_rows(real), hide_index=True)
    st.caption("Totais do portfólio real e do virtual nunca são somados.")
