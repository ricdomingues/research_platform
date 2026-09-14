"""Alpaca Market Data v2 minute bars (IEX, raw) and corporate-action splits over HTTP."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import httpx

from virtual_orders.marketdata.http import ResourceNotFound, get_json, vendor_decimal
from virtual_orders.marketdata.sources import (
    DataTier,
    RawBar,
    SourceDataError,
    SourceUnavailable,
    SplitRecord,
    TickerStatus,
)

ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_IEX_SOURCE = "alpaca_iex"
MAX_PAGES = 1000
ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


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
        seen: set[str] = set()
        while True:
            if len(pages) >= MAX_PAGES:
                raise SourceDataError(f"Alpaca pagination for {path} exceeded {MAX_PAGES} pages")
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
            next_token = body.get("next_page_token")
            if not next_token:
                return pages
            if not isinstance(next_token, str) or next_token in seen:
                raise SourceDataError(f"Alpaca repeated or invalid next_page_token for {path}")
            seen.add(next_token)
            token = next_token


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


class AlpacaAssets(_AlpacaClient):
    """Read-only Trading API asset lookup (spec 3.8, D14). Never calls account or order endpoints."""

    name = "alpaca_assets"

    def __init__(
        self,
        client: httpx.Client,
        key_id: str,
        secret_key: str,
        *,
        base_url: str = ALPACA_TRADING_URL,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(client, key_id, secret_key, base_url=base_url, attempts=attempts,
                         backoff_seconds=backoff_seconds, sleep=sleep)

    def check_ticker(self, ticker: str) -> TickerStatus:
        if not _SYMBOL.fullmatch(ticker):
            return TickerStatus(ticker, False, "INVALID_SYMBOL")
        try:
            body = get_json(
                self._client, f"{self._base_url}/v2/assets/{ticker}", params={}, headers=self._headers,
                attempts=self._attempts, backoff_seconds=self._backoff, sleep=self._sleep,
            )
        except ResourceNotFound as missing:
            if isinstance(missing.body, dict) and isinstance(missing.body.get("message"), str):
                return TickerStatus(ticker, False, "UNKNOWN_ASSET")
            raise SourceUnavailable(
                f"Alpaca asset endpoint answered 404 without an Alpaca error body for {ticker}"
            ) from None
        if not isinstance(body, dict):
            raise SourceDataError(f"malformed Alpaca asset for {ticker}")
        symbol, status = body.get("symbol"), body.get("status")
        tradable, asset_class = body.get("tradable"), body.get("class")
        if not (isinstance(symbol, str) and isinstance(status, str) and isinstance(tradable, bool)
                and isinstance(asset_class, str)):
            raise SourceDataError(f"malformed Alpaca asset for {ticker}")
        if symbol != ticker:
            raise SourceDataError(f"Alpaca asset symbol mismatch for {ticker}")
        if status != "active":
            return TickerStatus(ticker, False, "INACTIVE")
        if asset_class != "us_equity" or not tradable:
            return TickerStatus(ticker, False, "NOT_TRADABLE")
        return TickerStatus(ticker, True)
