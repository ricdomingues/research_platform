from __future__ import annotations

from functools import partial

import streamlit as st

from dashboard.client import ApiClient
from dashboard.viewmodels import InputError, rule_body, rule_rows, watchlist_ticker
from dashboard.views.common import guarded


def render(client: ApiClient) -> None:
    st.title("Watchlist e regras de alerta")
    st.caption("Alertas saem só pelo webhook n8n; pressão é estimativa derivada de OHLCV (D27).")
    with st.form("watchlist-add"):
        ticker = st.text_input("Ticker", key="watchlist-ticker")
        if st.form_submit_button("Adicionar à watchlist"):
            try:
                new_ticker = watchlist_ticker(ticker)  # M7: "/" never reaches /watchlist/{ticker}
            except InputError as exc:
                st.error(f"Entrada inválida: {exc.code}")
            else:
                added = guarded(lambda: client.add_ticker(new_ticker))
                if added is not None:
                    st.success(f"{added['ticker']}: {added['status']}")
    entries = guarded(client.watchlist)
    if entries is None:  # guarded already showed the error; no need for a second, misleading message
        return
    if not entries:
        st.info("Watchlist vazia.")
        return
    for entry in entries:
        symbol = str(entry["ticker"])
        with st.expander(f"{symbol} — {len(entry['rules'])} regra(s)"):
            st.dataframe(rule_rows(entry["rules"]), hide_index=True)
            for rule in entry["rules"]:
                rule_id = str(rule["id"])
                if st.button(f"Remover regra {rule['kind']} {rule_id[:8]}", key=f"rule-delete-{rule_id}"):
                    if guarded(partial(client.delete_rule, rule_id)) is not None:
                        st.success("Regra removida.")
            if st.button(f"Remover {symbol} da watchlist", key=f"watch-delete-{symbol}"):
                if guarded(partial(client.remove_ticker, symbol)) is not None:
                    st.success(f"{symbol} removido.")
            with st.form(f"rule-add-{symbol}"):
                kind = st.selectbox("Tipo", ["PRICE_CROSS", "PRESSURE"], key=f"rule-kind-{symbol}")
                level = st.text_input("Nível (PRICE_CROSS)", key=f"rule-level-{symbol}")
                direction = st.selectbox("Direção", ["ABOVE", "BELOW"], key=f"rule-direction-{symbol}")
                threshold = st.text_input("Limiar CMF (PRESSURE, entre 0 e 1)", key=f"rule-cmf-{symbol}")
                window = st.number_input("Janela (PRESSURE, candles)", min_value=5, max_value=390, value=30,
                                         key=f"rule-window-{symbol}")
                cooldown = st.number_input("Cooldown (min)", min_value=0, max_value=1440, value=30,
                                           key=f"rule-cooldown-{symbol}")
                if st.form_submit_button("Criar regra"):
                    try:
                        body = rule_body(kind=str(kind), level=level, direction=str(direction),
                                         cmf_threshold=threshold, window_bars=int(window),
                                         cooldown_minutes=int(cooldown))
                    except InputError as exc:
                        st.error(f"Entrada inválida: {exc.code}")
                    else:
                        if guarded(partial(client.create_rule, symbol, body)) is not None:
                            st.success("Regra criada.")
