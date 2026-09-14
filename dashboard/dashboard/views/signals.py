from __future__ import annotations

from datetime import date
from functools import partial

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import signal_rows, today_et
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Sinais do dia")
    day = st.date_input("Data (ET)", value=today_et(), key="signals-day")
    if not isinstance(day, date):
        return
    rows = guarded(lambda: client.signals(day=day))
    if rows is None:
        return
    if not rows:
        st.info("Nenhum sinal nesta data.")
        return
    st.dataframe(signal_rows(rows), hide_index=True)
    st.caption("Comprar virtual cria uma ordem MANUAL_USER; a API recusa sinais vencidos ou já decididos (spec 3.4.1).")
    for signal in rows:
        signal_id = str(signal["signal_id"])
        if st.button(f"Comprar virtual — {signal['ticker']} · {signal['strategy']}", key=f"buy-{signal_id}"):
            created = guarded(partial(client.create_manual_order, signal_id))
            if created is not None:
                st.success(f"Ordem virtual MANUAL_USER criada: {created['order_id']}")
