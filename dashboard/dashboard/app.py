"""Streamlit entry point: `streamlit run dashboard/app.py` from the dashboard project. API only (spec 2.1, D40)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import cast

import streamlit as st

from dashboard.client import ApiClient, DashboardConfigError
from dashboard.views import comparison, health, market, observation, orders, overview, portfolio, signals, watchlist

CLIENT_KEY = "api_client"
PAGES: dict[str, Callable[[ApiClient], None]] = {
    "Visão geral": overview.render,
    "Sinais do dia": signals.render,
    "Ordens": orders.render,
    "Comparação": comparison.render,
    "Saúde": health.render,
    "Watchlist e alertas": watchlist.render,
    "Mercado": market.render,
    "Portfólio": portfolio.render,
    "Observação": observation.render,
}


def _client() -> ApiClient | None:
    """One httpx client per browser session, never closed explicitly: accepted for a single owner (D40, M13)."""
    existing = st.session_state.get(CLIENT_KEY)
    if existing is not None:
        return cast(ApiClient, existing)  # injected by tests, or built on a previous rerun
    try:
        client = ApiClient.from_environment(os.environ)
    except DashboardConfigError as exc:
        st.error(f"Configuração do dashboard incompleta: {', '.join(exc.missing)}")
        return None
    st.session_state[CLIENT_KEY] = client
    return client


def main() -> None:
    st.set_page_config(page_title="Virtual Order Engine", layout="wide")
    client = _client()
    if client is None:
        return
    page = st.sidebar.radio("Página", list(PAGES), key="page")
    PAGES[str(page)](client)


main()
