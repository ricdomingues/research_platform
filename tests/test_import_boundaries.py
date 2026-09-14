"""Import boundaries: frozen pure core, and provider-neutral infrastructure (Plan 2 Global Constraints)."""

import ast
import importlib.util
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

PURE_PACKAGES = ["core/domain", "core/fills", "core/metrics"]
PURE_MODULES = ["core/actionability.py", "core/dataquality.py"]
IO_LIBRARIES = (
    "sqlalchemy", "psycopg", "alembic", "httpx", "yfinance", "pandas", "pandas_market_calendars",
    "requests", "urllib", "socket", "os", "pathlib",
)
CORE_ALLOWED = {"core/marketdata/nyse_calendar.py": {"pandas_market_calendars"}}

NEUTRAL_PACKAGES = [
    "virtual_orders/storage", "virtual_orders/ledger", "virtual_orders/evaluator", "virtual_orders/readmodels",
]
NEUTRAL_MARKETDATA = [
    "virtual_orders/marketdata/sources.py", "virtual_orders/marketdata/gateway.py",
    "virtual_orders/marketdata/calendars.py", "virtual_orders/marketdata/asof.py",
    "virtual_orders/marketdata/ingest.py", "virtual_orders/marketdata/snapshots.py",
]
PROVIDER_ADAPTERS = (
    "virtual_orders.marketdata.alpaca", "virtual_orders.marketdata.fmp",
    "virtual_orders.marketdata.yfinance_source", "virtual_orders.marketdata.http",
)
PROVIDER_LIBRARIES = ("httpx", "yfinance", "pandas")

COMPOSITION_ROOT = "virtual_orders/bootstrap.py"
ADAPTER_FILES = frozenset({
    "virtual_orders/marketdata/alpaca.py", "virtual_orders/marketdata/fmp.py",
    "virtual_orders/marketdata/yfinance_source.py", "virtual_orders/marketdata/http.py",
})
APPLICATION_LAYER = (
    "virtual_orders.api", "virtual_orders.bootstrap", "virtual_orders.config", "fastapi", "starlette", "uvicorn",
)
APPLICATION_NEUTRAL = ("virtual_orders/services.py", "virtual_orders/config.py")


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC))


def _pure_files() -> list[Path]:
    files = [SRC / module for module in PURE_MODULES]
    for package in PURE_PACKAGES:
        files.extend(sorted((SRC / package).rglob("*.py")))
    return files


def _core_files() -> list[Path]:
    return sorted((SRC / "core").rglob("*.py"))


def _neutral_files() -> list[Path]:
    files = [SRC / module for module in NEUTRAL_MARKETDATA if (SRC / module).exists()]
    for package in NEUTRAL_PACKAGES:
        if (SRC / package).exists():
            files.extend(sorted((SRC / package).rglob("*.py")))
    return files


def _platform_files() -> list[Path]:
    return sorted((SRC / "virtual_orders").rglob("*.py"))


def _api_files() -> list[Path]:
    return sorted((SRC / "virtual_orders/api").rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _offending(path: Path, prefixes: tuple[str, ...], allowed: set[str] | None = None) -> list[str]:
    allowed = allowed or set()
    return sorted(
        name for name in _imported_modules(path)
        if any(_matches(name, prefix) for prefix in prefixes) and not any(_matches(name, a) for a in allowed)
    )


@pytest.mark.parametrize("path", _pure_files(), ids=_rel)
def test_pure_core_module_has_no_io_or_platform_imports(path: Path) -> None:
    assert _offending(path, IO_LIBRARIES + ("virtual_orders",)) == []


@pytest.mark.parametrize("path", _core_files(), ids=_rel)
def test_frozen_core_never_imports_platform_or_io(path: Path) -> None:
    allowed = CORE_ALLOWED.get(_rel(path), set())
    assert _offending(path, IO_LIBRARIES + ("virtual_orders",), allowed) == []


@pytest.mark.parametrize("path", _neutral_files(), ids=_rel)
def test_neutral_infrastructure_never_imports_provider_adapters(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES) == []


def test_dataquality_does_not_import_fills() -> None:
    assert not any(_matches(name, "core.fills") for name in _imported_modules(SRC / "core/dataquality.py"))


def test_boundary_scan_sees_every_pure_file() -> None:
    assert len(_pure_files()) >= 12
    assert "core.domain.models" in _imported_modules(SRC / "core/fills/v1/engine.py")


def test_boundary_scan_covers_the_platform_packages() -> None:
    neutral = {_rel(p) for p in _neutral_files()}
    assert {
        "virtual_orders/evaluator/cycle.py", "virtual_orders/evaluator/manual.py", "virtual_orders/ledger/events.py",
        "virtual_orders/storage/tables.py", "virtual_orders/marketdata/asof.py", "virtual_orders/marketdata/gateway.py",
    } <= neutral
    for adapter in ("alpaca.py", "fmp.py", "yfinance_source.py", "http.py"):
        assert (SRC / "virtual_orders/marketdata" / adapter).exists()


@pytest.mark.parametrize(
    "path",
    [p for p in _platform_files() if _rel(p) not in ADAPTER_FILES | {COMPOSITION_ROOT}],
    ids=_rel,
)
def test_only_the_composition_root_imports_provider_adapters(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS) == []


@pytest.mark.parametrize("path", _api_files(), ids=_rel)
def test_api_never_imports_adapters_provider_libraries_or_the_composition_root(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES + ("virtual_orders.bootstrap",)) == []


@pytest.mark.parametrize("path", _neutral_files(), ids=_rel)
def test_neutral_infrastructure_never_imports_the_application_layer(path: Path) -> None:
    assert _offending(path, APPLICATION_LAYER) == []


@pytest.mark.parametrize(
    "path", [SRC / relative for relative in APPLICATION_NEUTRAL if (SRC / relative).exists()], ids=_rel
)
def test_services_and_config_never_import_adapters_or_provider_libraries(path: Path) -> None:
    assert _offending(path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES) == []


def test_boundary_scan_covers_the_application_layer() -> None:
    assert "virtual_orders/api/__init__.py" in {_rel(p) for p in _api_files()}
    assert "virtual_orders/readmodels/__init__.py" in {_rel(p) for p in _neutral_files()}
    assert importlib.util.find_spec("fastapi") is not None
    assert importlib.util.find_spec("uvicorn") is not None
    api = {_rel(p) for p in _api_files()}
    assert {
        "virtual_orders/api/app.py", "virtual_orders/api/intake.py", "virtual_orders/api/routes/health.py",
        "virtual_orders/api/routes/replay.py",
    } <= api
    neutral = {_rel(p) for p in _neutral_files()}
    assert {"virtual_orders/readmodels/health.py", "virtual_orders/readmodels/metrics.py"} <= neutral
    assert all((SRC / relative).exists() for relative in APPLICATION_NEUTRAL)
    assert (SRC / COMPOSITION_ROOT).exists()
    assert "virtual_orders.marketdata.alpaca" in _imported_modules(SRC / COMPOSITION_ROOT)
