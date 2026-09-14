from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaBars, AlpacaSplits
from virtual_orders.marketdata.fmp import FmpDividends
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.http import get_json
from virtual_orders.marketdata.sources import DataTier, SourceDataError, SourceUnavailable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def utc(hour: int, minute: int) -> datetime:
    return datetime(2025, 11, 25, hour, minute, tzinfo=UTC)


def test_alpaca_bars_paginates_parses_decimals_and_filters_end():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = "bars_page2.json" if request.url.params.get("page_token") else "bars_page1.json"
        return httpx.Response(200, text=fixture(f"alpaca/{page}"))

    source = AlpacaBars(client(handler), "key", "secret")
    bars = source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))

    assert [b.ts for b in bars] == [utc(14, 30), utc(14, 31), utc(14, 32)]
    assert bars[0].open == Decimal("101.25") and isinstance(bars[0].volume, Decimal)
    assert bars[0].to_bar().low == Decimal("101.1")
    first = seen[0]
    assert first.url.path == "/v2/stocks/AAPL/bars"
    assert first.headers["APCA-API-KEY-ID"] == "key"
    assert first.url.params["adjustment"] == "raw"
    assert first.url.params["feed"] == "iex"
    assert first.url.params["timeframe"] == "1Min"
    assert first.url.params["start"] == "2025-11-25T14:30:00Z"
    assert seen[1].url.params["page_token"] == "QUFQTHxNfDIwMjUtMTEtMjVUMTQ6MzI6MDBa"
    assert (source.provider, source.source, source.data_tier) == ("alpaca", ALPACA_IEX_SOURCE, DataTier.RESEARCH)


def test_retry_with_backoff_then_success():
    sleeps: list[float] = []
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, text='{"ok": 1.5}')])
    body = get_json(client(lambda request: next(responses)), "https://x.test/a", params={},
                    sleep=sleeps.append)
    assert body == {"ok": Decimal("1.5")}
    assert sleeps == [1.0, 2.0]


def test_retry_exhaustion_raises_unavailable():
    sleeps: list[float] = []
    with pytest.raises(SourceUnavailable, match="after 3 attempts"):
        get_json(client(lambda request: httpx.Response(500)), "https://x.test/a", params={},
                 sleep=sleeps.append)
    assert sleeps == [1.0, 2.0]


def test_transport_errors_are_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text="[]")

    assert get_json(client(handler), "https://x.test/a", params={}, sleep=lambda s: None) == []
    assert len(calls) == 2


def test_non_retryable_status_fails_immediately():
    sleeps: list[float] = []
    with pytest.raises(SourceUnavailable, match="HTTP 403"):
        get_json(client(lambda request: httpx.Response(403)), "https://x.test/a", params={},
                 sleep=sleeps.append)
    assert sleeps == []


def test_invalid_vendor_values_raise_data_error():
    bad = '{"bars":[{"t":"2025-11-25T14:30:00Z","o":"abc","h":1,"l":1,"c":1,"v":1}],"next_page_token":null}'
    source = AlpacaBars(client(lambda request: httpx.Response(200, text=bad)), "k", "s")
    with pytest.raises(SourceDataError):
        source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))


def test_alpaca_splits_parses_forward_and_reverse():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=fixture("alpaca/corporate_actions.json"))

    splits = AlpacaSplits(client(handler), "k", "s").fetch_splits(["XYZ", "NVDA"], date(2024, 1, 1), date(2025, 12, 31))
    assert [(s.ticker, s.ex_date, s.old_rate, s.new_rate) for s in splits] == [
        ("NVDA", date(2024, 6, 10), Decimal(1), Decimal(10)),
        ("XYZ", date(2025, 11, 26), Decimal(20), Decimal(1)),
    ]
    assert seen[0].url.path == "/v1/corporate-actions"
    assert seen[0].url.params["symbols"] == "NVDA,XYZ"
    assert seen[0].url.params["types"] == "forward_split,reverse_split"
    assert AlpacaSplits(client(handler), "k", "s").fetch_splits([], date(2024, 1, 1), date(2024, 1, 2)) == []


def test_fmp_dividends_filters_by_ex_date_and_parses_optional_pay_date():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=fixture("fmp/dividends.json"))

    source = FmpDividends(client(handler), "fmp-key")
    records = source.fetch_dividends("AAPL", date(2025, 8, 11), date(2025, 11, 10))
    assert [(r.ex_date, r.amount, r.pay_date) for r in records] == [
        (date(2025, 8, 11), Decimal("0.26"), None),
        (date(2025, 11, 10), Decimal("0.26"), date(2025, 11, 13)),
    ]
    assert seen[0].url.path == "/stable/dividends"
    assert seen[0].url.params["symbol"] == "AAPL" and seen[0].url.params["apikey"] == "fmp-key"
    assert source.name == "fmp"


class _Feed:
    provider, provider_version, data_tier = "any", "t", DataTier.RESEARCH

    def __init__(self, source: str) -> None:
        self.source = source

    def fetch_bars(self, ticker, start, end):
        return []


def test_gateway_resolves_exact_source_without_fallback():
    primary, backup = _Feed("primary_feed"), _Feed("backup_feed")
    gateway = MarketDataGateway([primary, backup])
    assert gateway.bar_source("primary_feed") is primary
    assert gateway.source_ids == ("backup_feed", "primary_feed")
    with pytest.raises(UnknownDataSource, match="fallback is not allowed"):
        MarketDataGateway([backup]).bar_source("primary_feed")


def test_gateway_rejects_duplicate_source_ids():
    with pytest.raises(ValueError, match="duplicate"):
        MarketDataGateway([_Feed("same"), _Feed("same")])


def test_fixture_manifest_labels_every_fixture_as_synthetic():
    manifest = (FIXTURES / "README.md").read_text()
    labelled = {
        line.split("`")[1] for line in manifest.splitlines()
        if line.startswith("| `") and "synthetic/documentation-derived fixture" in line
    }
    files = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*") if p.is_file() and p.name != "README.md"}
    assert files and files <= labelled
