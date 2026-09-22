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


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not allowed")


def json_text(body: Any) -> str:
    """JSON with Decimal written as exact number tokens (the API reads numbers as Decimal, never float)."""
    numbers: dict[str, str] = {}

    def default(value: Any) -> str:
        if isinstance(value, Decimal):
            marker = f"__decimal_{len(numbers)}__"
            numbers[marker] = format(value, "f")
            return marker
        raise TypeError(f"not JSON serializable: {type(value).__name__}")

    try:
        text = json.dumps(body, default=default, allow_nan=False)
    except ValueError:
        # No exception text: it can echo the offending nan/inf value back.
        raise ApiRequestFailed(0, "INVALID_REQUEST") from None
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
            payload: Any = (
                json.loads(response.text, parse_float=Decimal, parse_constant=_reject_constant)
                if response.content else {}
            )
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

    def observation_report(self, day: date) -> JsonObject:
        return self._request("GET", "/observation/report", params={"day": day})

    def observation_summary(self, start: date, end: date) -> JsonObject:
        return self._request("GET", "/observation/summary", params={"from": start, "to": end})

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

    # Research (Plan 5). Read-only: the dashboard looks at what the scan recorded and never starts one.
    def research_versions(self) -> JsonObject:
        return self._request("GET", "/research/versions")

    def research_runs(self, *, kind: str | None = None, limit: int = 20) -> list[JsonObject]:
        return list(self._request("GET", "/research/runs", params={"kind": kind, "limit": limit})["runs"])

    def research_candidates(
        self,
        *,
        ticker: str | None = None,
        timeframe: str | None = None,
        pattern: str | None = None,
        direction: str | None = None,
        min_score: Decimal | None = None,
        levels_valid: bool | None = None,
        limit: int = 200,
    ) -> JsonObject:
        """The whole envelope, not just the rows: the score's documented meaning travels with the numbers."""
        return self._request("GET", "/research/candidates", params={
            "ticker": ticker, "timeframe": timeframe, "pattern": pattern, "direction": direction,
            "min_score": min_score, "levels_valid": levels_valid, "limit": limit,
        })

    def research_candidate(self, candidate_id: int) -> JsonObject:
        return self._request("GET", f"/research/candidates/{_segment(str(candidate_id))}")

    def research_detections(
        self, *, ticker: str | None = None, timeframe: str | None = None, pattern: str | None = None,
        limit: int = 200,
    ) -> list[JsonObject]:
        return list(self._request("GET", "/research/detections", params={
            "ticker": ticker, "timeframe": timeframe, "pattern": pattern, "limit": limit,
        })["detections"])

    def research_markers(
        self, ticker: str, timeframe: str, start: datetime | str, end: datetime | str, *, limit: int = 500
    ) -> list[JsonObject]:
        return list(self._request("GET", "/research/markers", params={
            "ticker": ticker, "timeframe": timeframe, "from": start, "to": end, "limit": limit,
        })["markers"])

    def promote_candidate(
        self, candidate_id: int, *, override: bool = False, auto_order: bool = True
    ) -> JsonObject:
        """Promote one candidate through the engine's own boundary. A refusal raises with its reasons."""
        return self._request("POST", f"/research/candidates/{int(candidate_id)}/promote",
                             body={"override": override, "auto_order": auto_order})

    def research_backtests(
        self, *, pattern: str | None = None, timeframe: str | None = None, split: str | None = None,
        ticker: str | None = None, limit: int = 100,
    ) -> list[JsonObject]:
        return list(self._request("GET", "/research/backtests", params={
            "pattern": pattern, "timeframe": timeframe, "split": split, "ticker": ticker, "limit": limit,
        })["backtests"])

    def research_models(self, *, limit: int = 20) -> list[JsonObject]:
        return list(self._request("GET", "/research/models", params={"limit": limit})["models"])
