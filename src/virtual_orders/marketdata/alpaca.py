"""Alpaca Market Data v2 minute bars (IEX, raw) and corporate-action splits over HTTP."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import httpx

from virtual_orders.marketdata.http import get_json, vendor_decimal
from virtual_orders.marketdata.sources import DataTier, RawBar, SourceDataError, SplitRecord

ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_IEX_SOURCE = "alpaca_iex"


def _iso_z(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SourceDataError(f"timestamp is not a string: {value!r}")
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise SourceDataError(f"timestamp without timezone: {value!r}")
    return ts.astimezone(UTC)


class _AlpacaClient:
    def __init__(
        self,
        client: httpx.Client,
        key_id: str,
        secret_key: str,
        *,
        base_url: str = ALPACA_DATA_URL,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._base_url = base_url.rstrip("/")
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._sleep = sleep

    def _pages(self, path: str, params: dict[str, str | int]) -> list[Any]:
        pages: list[Any] = []
        token: str | None = None
        while True:
            page_params = dict(params)
            if token:
                page_params["page_token"] = token
            body = get_json(
                self._client, f"{self._base_url}{path}", params=page_params, headers=self._headers,
                attempts=self._attempts, backoff_seconds=self._backoff, sleep=self._sleep,
            )
            if not isinstance(body, dict):
                raise SourceDataError(f"unexpected Alpaca body for {path}")
            pages.append(body)
            token = body.get("next_page_token")
            if not token:
                return pages


class AlpacaBars(_AlpacaClient):
    provider = "alpaca"
    provider_version = "market-data-v2/bars/iex/raw"
    data_tier = DataTier.RESEARCH
    source = ALPACA_IEX_SOURCE

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[RawBar]:
        params: dict[str, str | int] = {
            "timeframe": "1Min", "start": _iso_z(start), "end": _iso_z(end), "adjustment": "raw",
            "feed": "iex", "limit": 10000, "sort": "asc",
        }
        bars: list[RawBar] = []
        for page in self._pages(f"/v2/stocks/{ticker}/bars", params):
            for item in page.get("bars") or []:
                try:
                    ts = _parse_ts(item["t"])
                    raw = RawBar(
                        ticker=ticker, ts=ts,
                        open=vendor_decimal(item["o"], "o"), high=vendor_decimal(item["h"], "h"),
                        low=vendor_decimal(item["l"], "l"), close=vendor_decimal(item["c"], "c"),
                        volume=vendor_decimal(item["v"], "v"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise SourceDataError(f"malformed Alpaca bar for {ticker}: {item!r}") from exc
                if start <= ts < end:
                    bars.append(raw)
        return bars


class AlpacaSplits(_AlpacaClient):
    def fetch_splits(self, tickers: Sequence[str], start: date, end: date) -> list[SplitRecord]:
        if not tickers:
            return []
        params: dict[str, str | int] = {
            "symbols": ",".join(sorted(set(tickers))), "types": "forward_split,reverse_split",
            "start": start.isoformat(), "end": end.isoformat(), "limit": 1000,
        }
        records: list[SplitRecord] = []
        for page in self._pages("/v1/corporate-actions", params):
            actions = page.get("corporate_actions") or {}
            for kind in ("forward_splits", "reverse_splits"):
                for item in actions.get(kind) or []:
                    try:
                        records.append(SplitRecord(
                            ticker=item["symbol"], ex_date=date.fromisoformat(item["ex_date"]),
                            old_rate=vendor_decimal(item["old_rate"], "old_rate"),
                            new_rate=vendor_decimal(item["new_rate"], "new_rate"),
                        ))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise SourceDataError(f"malformed Alpaca split: {item!r}") from exc
        return sorted(records, key=lambda r: (r.ex_date, r.ticker))
