"""Ticker path/query normalization shared by the routes (D26, D42): the same rule as the signal validation."""

from __future__ import annotations

from virtual_orders.api.errors import ApiError


def normalize_ticker(ticker: str) -> str:
    """D26: stripped and upper-cased, so the result satisfies the signal rule (core TICKER_INVALID)."""
    normalized = ticker.strip().upper()
    if not normalized or any(character.isspace() for character in normalized):
        raise ApiError(422, "TICKER_INVALID", detail={"ticker": ticker})
    return normalized
