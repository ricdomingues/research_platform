"""Helpers shared by every view (D40)."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from dashboard.viewmodels import describe_api_error


def guarded[T](action: Callable[[], T]) -> T | None:
    """Runs one API call; any failure becomes a fixed message on screen, never a traceback or exception text."""
    try:
        return action()
    except Exception as exc:  # noqa: BLE001 - the owner's screen shows codes or exception types only (D40)
        st.error(describe_api_error(exc))
        return None
