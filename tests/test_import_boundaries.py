"""Import boundaries: frozen pure core, and provider-neutral infrastructure (Plan 2 Global Constraints)."""

import ast
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

NEUTRAL_PACKAGES = ["virtual_orders/storage", "virtual_orders/ledger", "virtual_orders/evaluator"]
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
