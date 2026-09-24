from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaAssets, AlpacaBars, AlpacaSplits
from virtual_orders.marketdata.fmp import FmpDividends
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.http import ResourceNotFound, get_json
from virtual_orders.marketdata.sources import DataTier, SourceDataError, SourceUnavailable, TickerStatus

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
    """Every provider-format fixture is documentation-derived, never a captured network response.

    `research/` fixtures are a different kind of thing: real, sanitized rows from our own database rather
    than a provider's API response (Plan 6 task 8, `canary_retraction.json`), so they carry their own label
    in the manifest instead of claiming to be synthetic.
    """
    manifest = (FIXTURES / "README.md").read_text()
    rows = {
        line.split("`")[1]: line for line in manifest.splitlines() if line.startswith("| `")
    }
    files = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*") if p.is_file() and p.name != "README.md"}
    assert files and files <= set(rows)  # every fixture is accounted for in the manifest

    provider_files = {f for f in files if not f.startswith("research/")}
    assert provider_files and all("synthetic/documentation-derived fixture" in rows[f] for f in provider_files)

    real_files = files - provider_files
    assert real_files and all("real fixture" in rows[f] for f in real_files)


def test_alpaca_repeated_page_token_fails_instead_of_looping():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) > 5:  # self-terminating: without the fix the test fails here instead of looping forever
            raise AssertionError("pagination looped")
        return httpx.Response(200, text='{"bars": [], "next_page_token": "same"}')

    source = AlpacaBars(client(handler), "key", "secret")
    with pytest.raises(SourceDataError, match="repeated or invalid next_page_token"):
        source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))
    assert len(calls) == 2


def test_alpaca_pagination_is_capped(monkeypatch):
    import virtual_orders.marketdata.alpaca as alpaca_module

    monkeypatch.setattr(alpaca_module, "MAX_PAGES", 3)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) > 10:  # self-terminating without the cap
            raise AssertionError("pagination was not capped")
        return httpx.Response(200, text=f'{{"bars": [], "next_page_token": "t{len(calls)}"}}')

    source = AlpacaBars(client(handler), "key", "secret")
    with pytest.raises(SourceDataError, match="exceeded 3 pages"):
        source.fetch_bars("AAPL", utc(14, 30), utc(14, 40))


def test_http_404_is_a_distinct_not_found_error():
    with pytest.raises(ResourceNotFound, match="HTTP 404"):
        get_json(client(lambda request: httpx.Response(404)), "https://x.test/a", params={}, sleep=lambda s: None)
    assert issubclass(ResourceNotFound, SourceUnavailable)


def test_alpaca_assets_reports_an_active_tradable_ticker_read_only():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        symbol = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=fixture("alpaca/asset_active.json").replace('"AAPL"', f'"{symbol}"'))

    check = AlpacaAssets(client(handler), "key", "secret")
    assert check.check_ticker("AAPL") == TickerStatus("AAPL", True)
    assert check.check_ticker("BRK.B") == TickerStatus("BRK.B", True)
    assert [(r.method, r.url.host, r.url.path) for r in seen] == [
        ("GET", "paper-api.alpaca.markets", "/v2/assets/AAPL"),
        ("GET", "paper-api.alpaca.markets", "/v2/assets/BRK.B"),
    ]
    assert seen[0].headers["APCA-API-KEY-ID"] == "key"
    assert check.name == "alpaca_assets"


def test_alpaca_assets_inactive_unknown_untradable_and_invalid_symbols():
    inactive = AlpacaAssets(client(lambda r: httpx.Response(200, text=fixture("alpaca/asset_inactive.json"))), "k", "s")
    assert inactive.check_ticker("ZZZZ") == TickerStatus("ZZZZ", False, "INACTIVE")

    untradable_body = fixture("alpaca/asset_active.json").replace('"tradable": true', '"tradable": false')
    untradable = AlpacaAssets(client(lambda r: httpx.Response(200, text=untradable_body)), "k", "s")
    assert untradable.check_ticker("AAPL") == TickerStatus("AAPL", False, "NOT_TRADABLE")

    crypto_body = fixture("alpaca/asset_active.json").replace('"us_equity"', '"crypto"')
    crypto = AlpacaAssets(client(lambda r: httpx.Response(200, text=crypto_body)), "k", "s")
    assert crypto.check_ticker("AAPL") == TickerStatus("AAPL", False, "NOT_TRADABLE")

    missing = AlpacaAssets(client(lambda r: httpx.Response(404, text='{"message": "not found"}')), "k", "s")
    assert missing.check_ticker("NOPE") == TickerStatus("NOPE", False, "UNKNOWN_ASSET")

    calls = []
    guarded = AlpacaAssets(client(lambda r: calls.append(r) or httpx.Response(200)), "k", "s")
    for symbol in ("../v2/orders", "aapl", "", "TOOLONGSYMBOL1"):
        assert guarded.check_ticker(symbol) == TickerStatus(symbol, False, "INVALID_SYMBOL")
    assert calls == []


def test_alpaca_assets_unavailable_or_malformed_raise():
    down = AlpacaAssets(client(lambda r: httpx.Response(503)), "k", "s", sleep=lambda s: None)
    with pytest.raises(SourceUnavailable):
        down.check_ticker("AAPL")
    malformed = AlpacaAssets(client(lambda r: httpx.Response(200, text='{"status": "active"}')), "k", "s")
    with pytest.raises(SourceDataError, match="malformed Alpaca asset"):
        malformed.check_ticker("AAPL")
    other_asset = AlpacaAssets(client(lambda r: httpx.Response(200, text=fixture("alpaca/asset_active.json"))), "k", "s")
    with pytest.raises(SourceDataError, match="Alpaca asset symbol mismatch for MSFT"):
        other_asset.check_ticker("MSFT")
