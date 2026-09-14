from __future__ import annotations

from functools import partial

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import comparison_rows, replay_pairs
from dashboard.views.common import guarded

COMPARISONS = (("origin", "AUTO × MANUAL"), ("strategy", "Estratégia"),
               ("fill_model_version", "Versão do fill model"), ("entry_path", "entry_path"))


def render(client: ApiClient) -> None:
    st.title("Comparação")
    include = st.toggle("Incluir ordens em revisão", value=False, key="comparison-include-review")
    for group_by, title in COMPARISONS:
        payload = guarded(partial(client.metrics, group_by=group_by, include_needs_review=include))
        if payload is not None:
            st.subheader(title)
            st.dataframe(comparison_rows(payload["groups"]), hide_index=True)
    st.subheader("Original × replay")
    originals = guarded(lambda: client.orders(limit=1000))
    replays = guarded(lambda: client.orders(replay=True, limit=1000))
    if originals is None or replays is None:
        return
    pairs = replay_pairs(originals, replays)
    if not pairs:
        st.info("Nenhum replay.")
        return
    st.dataframe(pairs, hide_index=True)
    selected = st.selectbox("Configuração do fill model", [pair["Replay"] for pair in pairs], key="comparison-replay")
    pair = next((item for item in pairs if item["Replay"] == selected), None)
    if pair is None:
        return
    source_chart = guarded(lambda: client.order_chart(pair["Original"]))
    replay_chart = guarded(lambda: client.order_chart(pair["Replay"]))
    left, right = st.columns(2)
    for column, label, chart in ((left, "Original", source_chart), (right, "Replay", replay_chart)):
        if chart is not None:
            column.markdown(f"**{label}** · {chart['fill_model_version']}")
            column.json(chart["config_snapshot"])
