"""FMP dividends (free tier `stable/dividends`)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

import httpx

from virtual_orders.marketdata.http import get_json, vendor_decimal
from virtual_orders.marketdata.sources import DividendRecord, SourceDataError

FMP_URL = "https://financialmodelingprep.com"


class FmpDividends:
    name = "fmp"

    def __init__(
        self,
        client: httpx.Client,
        api_key: str,
        *,
        base_url: str = FMP_URL,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._sleep = sleep

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]:
        body = get_json(
            self._client, f"{self._base_url}/stable/dividends",
            params={"symbol": ticker, "apikey": self._api_key},
            attempts=self._attempts, backoff_seconds=self._backoff, sleep=self._sleep,
        )
        if not isinstance(body, list):
            raise SourceDataError(f"unexpected FMP dividends body for {ticker}")
        records: list[DividendRecord] = []
        for item in body:
            try:
                ex_date = date.fromisoformat(item["date"])
                pay_raw = item.get("paymentDate")
                record = DividendRecord(
                    ticker=ticker, ex_date=ex_date,
                    amount=vendor_decimal(item["dividend"], "dividend"),
                    pay_date=date.fromisoformat(pay_raw) if pay_raw else None,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SourceDataError(f"malformed FMP dividend for {ticker}: {item!r}") from exc
            if start <= ex_date <= end:
                records.append(record)
        return sorted(records, key=lambda r: r.ex_date)
