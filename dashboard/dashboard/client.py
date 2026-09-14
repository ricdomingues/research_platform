"""HTTP client for the Virtual Order Engine API. The dashboard talks only to the API (spec 2.1, D40)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

API_URL_VARIABLE = "DASHBOARD_API_URL"
API_KEY_VARIABLE = "API_KEY"
API_KEY_HEADER = "X-API-Key"
TIMEOUT_SECONDS = 15.0

JsonObject = dict[str, Any]


class DashboardConfigError(Exception):
    """Missing dashboard settings, named by variable, never by value."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(", ".join(missing))
        self.missing = missing


class ApiRequestFailed(Exception):
    """A non-2xx answer: HTTP status and the envelope's fixed code, reason and detail (spec 5.1, D19)."""

    def __init__(
        self, status_code: int, code: str, reason: str | None = None, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.reason = reason
        self.detail: dict[str, Any] = dict(detail or {})


class ApiUnreachable(Exception):
    """Transport failure. Only the httpx exception type is kept: its message can carry the URL."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


def json_text(body: Any) -> str:
    """JSON with Decimal written as exact number tokens (the API reads numbers as Decimal, never float)."""
    numbers: dict[str, str] = {}

    def default(value: Any) -> str:
        if isinstance(value, Decimal):
            marker = f"__decimal_{len(numbers)}__"
            numbers[marker] = format(value, "f")
            return marker
        raise TypeError(f"not JSON serializable: {type(value).__name__}")

    text = json.dumps(body, default=default)
    for marker, number in numbers.items():
        text = text.replace(f'"{marker}"', number)
    return text


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime in an API query")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal | int | str):
        return str(value)
    raise TypeError(f"unsupported query value: {type(value).__name__}")


def _params(params: Mapping[str, Any]) -> dict[str, str]:
    return {name: _param(value) for name, value in params.items() if value is not None}


def _segment(value: str) -> str:
    return quote(value, safe="")


class ApiClient:
    def __init__(
        self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._client = httpx.Client(base_url=base_url, headers={API_KEY_HEADER: api_key}, timeout=timeout,
                                    transport=transport)

    def __repr__(self) -> str:
        return f"ApiClient(base_url={str(self._client.base_url)!r})"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> ApiClient:
        url = environ.get(API_URL_VARIABLE, "").strip()
        key = environ.get(API_KEY_VARIABLE, "").strip()
        missing = [f"MISSING:{name}" for name, value in ((API_URL_VARIABLE, url), (API_KEY_VARIABLE, key)) if not value]
        if missing:
            raise DashboardConfigError(missing)
        return cls(url, key)

    def close(self) -> None:
        self._client.close()

    def _request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None, body: Any = None,
        accepted: tuple[int, ...] = (),
    ) -> JsonObject:
        content = None if body is None else json_text(body)
        headers = None if body is None else {"Content-Type": "application/json"}
        query = _params(params or {})
        try:
            response = self._client.request(method, path, params=query, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiUnreachable(type(exc).__name__) from None
        try:
            payload: Any = json.loads(response.text, parse_float=Decimal) if response.content else {}
        except ValueError:
            payload = None
        status = response.status_code
        if 200 <= status < 300 or status in accepted:
            if isinstance(payload, dict):
                return payload
            raise ApiRequestFailed(status, "INVALID_RESPONSE")
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            raise ApiRequestFailed(status, f"HTTP_{status}")
        reason, detail = error.get("reason"), error.get("detail")
        raise ApiRequestFailed(status, str(error.get("code") or f"HTTP_{status}"),
                               reason if isinstance(reason, str) else None, detail if isinstance(detail, dict) else None)

    def health(self) -> JsonObject:
        return self._request("GET", "/health", accepted=(503,))  # UNHEALTHY still carries state and causes (D17)

    def health_log(self, *, limit: int = 20) -> list[JsonObject]:
        return list(self._request("GET", "/health/log", params={"limit": limit})["entries"])

    def alert_outbox(self, *, outcome: str | None = None, limit: int = 100) -> list[JsonObject]:
        return list(self._request("GET", "/alert-outbox", params={"outcome": outcome, "limit": limit})["alerts"])

    def quality_overview(self) -> JsonObject:
        return self._request("GET", "/quality/overview")

    def metrics(
        self, *, group_by: str | None = None, include_needs_review: bool = False, replay: bool = False,
        start: datetime | None = None, end: datetime | None = None,
    ) -> JsonObject:
        return self._request("GET", "/metrics", params={
            "group_by": group_by, "include_needs_review": include_needs_review, "replay": replay, "from": start,
            "to": end,
        })

    def signals(self, *, day: date | None = None, strategy: str | None = None, limit: int = 200) -> list[JsonObject]:
        return list(self._request("GET", "/signals", params={"date": day, "strategy": strategy,
                                                             "limit": limit})["signals"])

    def create_manual_order(self, signal_id: str) -> JsonObject:
        return self._request("POST", f"/signals/{_segment(signal_id)}/orders")

    def orders(
        self, *, status: str | None = None, origin: str | None = None, strategy: str | None = None,
        replay: bool = False, needs_review: bool | None = None, limit: int = 200,
    ) -> list[JsonObject]:
        return list(self._request("GET", "/orders", params={
            "status": status, "origin": origin, "strategy": strategy, "replay": replay, "needs_review": needs_review,
            "limit": limit,
        })["orders"])

    def order_detail(self, order_id: str) -> JsonObject:
        return self._request("GET", f"/orders/{_segment(order_id)}")

    def order_chart(self, order_id: str) -> JsonObject:
        return self._request("GET", f"/orders/{_segment(order_id)}/chart")

    def market_bars(
        self, ticker: str, start: datetime | str, end: datetime | str, *, source: str | None = None
    ) -> JsonObject:
        return self._request("GET", "/market/bars", params={"ticker": ticker, "from": start, "to": end,
                                                            "source": source})

    def pressure(self, ticker: str, *, window_bars: int = 30, cmf_threshold: Decimal | None = None) -> JsonObject:
        return self._request("GET", "/market/pressure", params={"ticker": ticker, "window_bars": window_bars,
                                                                "cmf_threshold": cmf_threshold})

    def watchlist(self) -> list[JsonObject]:
        return list(self._request("GET", "/watchlist")["watchlist"])

    def add_ticker(self, ticker: str) -> JsonObject:
        return self._request("PUT", f"/watchlist/{_segment(ticker)}")

    def remove_ticker(self, ticker: str) -> JsonObject:
        return self._request("DELETE", f"/watchlist/{_segment(ticker)}")

    def create_rule(self, ticker: str, body: Mapping[str, Any]) -> JsonObject:
        return dict(self._request("POST", f"/watchlist/{_segment(ticker)}/alerts", body=dict(body))["rule"])

    def delete_rule(self, rule_id: str) -> JsonObject:
        return self._request("DELETE", f"/alerts/{_segment(rule_id)}")

    def virtual_portfolio(self) -> JsonObject:
        return self._request("GET", "/portfolio/virtual")

    def real_portfolio(self) -> JsonObject:
        return self._request("GET", "/portfolio/real")
