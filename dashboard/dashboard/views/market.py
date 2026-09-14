from __future__ import annotations

from datetime import date

import streamlit as st

from dashboard.charts import candlestick_figure
from dashboard.client import ApiClient
from dashboard.viewmodels import (
    ORDER_LIST_LIMIT,
    market_day_window,
    market_tickers,
    order_label,
    pressure_display,
    today_et,
)
from dashboard.views.common import guarded

NO_ORDER = "(nenhuma)"


def render(client: ApiClient) -> None:
    st.title("Mercado")
    watched = guarded(client.watchlist) or []
    orders = guarded(lambda: client.orders(limit=ORDER_LIST_LIMIT)) or []
    options = market_tickers(watched, orders)
    if not options:
        st.info("Adicione um ticker à watchlist ou crie uma ordem para ver o mercado.")
        return
    ticker = str(st.selectbox("Ticker", options, key="market-ticker"))
    day = st.date_input("Pregão (ET)", value=today_et(), key="market-day")
    if not isinstance(day, date):
        return
    start, end = market_day_window(day)
    related = {str(o["order_id"]): o for o in orders if o["ticker"] == ticker}
    overlay = st.selectbox("Sobrepor ordem", [NO_ORDER, *related], key="market-overlay",
                           format_func=lambda oid: oid if oid == NO_ORDER else order_label(related[oid]))
    chart = None if overlay in (None, NO_ORDER) else guarded(lambda: client.order_chart(str(overlay)))
    source = None if chart is None else str(chart["price_source"])  # M6: the overlaid order's own feed
    bars = guarded(lambda: client.market_bars(ticker, start, end, source=source))
    if bars is not None:
        if bars["bars"]:
            st.plotly_chart(candlestick_figure(
                bars["bars"], bars["vwap"], title=f"{ticker} · {day.isoformat()}",
                levels=None if chart is None else chart["levels"], markers=() if chart is None else chart["markers"],
                evaluation_start_ts=None if chart is None else chart["evaluation_start_ts"],
            ))
        else:
            st.info("Nenhum candle armazenado neste pregão.")
        st.caption(f"Candles armazenados lidos as-of {bars['data_as_of']}; VWAP: {bars['vwap_method']}.")
    window = st.number_input("Janela da estimativa (candles)", min_value=5, max_value=390, value=30,
                             key="market-window")
    payload = guarded(lambda: client.pressure(ticker, window_bars=int(window)))
    if payload is not None:
        display = pressure_display(payload)
        st.subheader(display.title)
        for line in display.lines:
            st.write(line)
        st.caption(f"Método: {display.method}")
        st.caption(display.disclaimer)
