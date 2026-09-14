"""Import boundaries: frozen pure core, and provider-neutral infrastructure (Plan 2 Global Constraints)."""

import ast
import importlib.util
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
ROOT = SRC.parent

PURE_PACKAGES = ["core/domain", "core/fills", "core/metrics"]
PURE_MODULES = ["core/actionability.py", "core/dataquality.py"]
IO_LIBRARIES = (
    "sqlalchemy", "psycopg", "alembic", "httpx", "yfinance", "pandas", "pandas_market_calendars",
    "requests", "urllib", "socket", "os", "pathlib",
)
CORE_ALLOWED = {"core/marketdata/nyse_calendar.py": {"pandas_market_calendars"}}

NEUTRAL_PACKAGES = [
    "virtual_orders/storage", "virtual_orders/ledger", "virtual_orders/evaluator", "virtual_orders/readmodels",
    "virtual_orders/alerts", "virtual_orders/analytics",
]
NEUTRAL_MARKETDATA = [
    "virtual_orders/marketdata/sources.py", "virtual_orders/marketdata/gateway.py",
    "virtual_orders/marketdata/calendars.py", "virtual_orders/marketdata/asof.py",
    "virtual_orders/marketdata/ingest.py", "virtual_orders/marketdata/snapshots.py",
]
PROVIDER_ADAPTERS = (
    "virtual_orders.marketdata.alpaca", "virtual_orders.marketdata.fmp",
    "virtual_orders.marketdata.yfinance_source", "virtual_orders.marketdata.http",
    "virtual_orders.notify.n8n",
)
PROVIDER_LIBRARIES = ("httpx", "yfinance", "pandas")

COMPOSITION_ROOT = "virtual_orders/bootstrap.py"
ADAPTER_FILES = frozenset({
    "virtual_orders/marketdata/alpaca.py", "virtual_orders/marketdata/fmp.py",
    "virtual_orders/marketdata/yfinance_source.py", "virtual_orders/marketdata/http.py",
    "virtual_orders/notify/n8n.py",
})
APPLICATION_LAYER = (
    "virtual_orders.api", "virtual_orders.bootstrap", "virtual_orders.config", "virtual_orders.worker",
    "fastapi", "starlette", "uvicorn",
)
APPLICATION_NEUTRAL = ("virtual_orders/services.py", "virtual_orders/config.py")
WORKER_PACKAGE = "virtual_orders/worker"
WORKER_ENTRYPOINT = "virtual_orders/worker/__main__.py"
PLATFORM_PURE_MODULES = [
    "virtual_orders/analytics/pressure.py", "virtual_orders/alerts/rules.py",
    "virtual_orders/analytics/vwap.py", "virtual_orders/analytics/portfolio.py",
]
PLATFORM_INFRASTRUCTURE = (
    "virtual_orders.storage", "virtual_orders.ledger", "virtual_orders.evaluator", "virtual_orders.readmodels",
    "virtual_orders.marketdata", "virtual_orders.api", "virtual_orders.worker", "virtual_orders.bootstrap",
    "virtual_orders.config", "virtual_orders.services", "virtual_orders.notify",
)


def _worker_files() -> list[Path]:
    return sorted((SRC / WORKER_PACKAGE).rglob("*.py"))


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
    assert _offending(
        path,
        PROVIDER_ADAPTERS + PROVIDER_LIBRARIES
        + ("virtual_orders.api", "virtual_orders.bootstrap", "fastapi", "starlette", "uvicorn"),
    ) == []


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


@pytest.mark.parametrize("path", _worker_files(), ids=_rel)
def test_worker_never_imports_adapters_provider_libraries_or_the_http_layer(path: Path) -> None:
    assert _offending(
        path, PROVIDER_ADAPTERS + PROVIDER_LIBRARIES + ("virtual_orders.api", "fastapi", "starlette", "uvicorn"),
    ) == []


@pytest.mark.parametrize("path", [p for p in _worker_files() if _rel(p) != WORKER_ENTRYPOINT], ids=_rel)
def test_only_the_worker_entrypoint_imports_the_composition_root(path: Path) -> None:
    assert _offending(path, ("virtual_orders.bootstrap", "virtual_orders.config")) == []


def test_platform_pure_modules_have_no_io_or_infrastructure_imports() -> None:
    # Not parametrized on purpose: until Tasks 11 and 13 create the modules an empty parameter set would be
    # reported as a skip, and every full run must be skip-free. Task 17 pins that both files exist.
    existing = [SRC / relative for relative in PLATFORM_PURE_MODULES if (SRC / relative).exists()]
    assert {_rel(path): _offending(path, IO_LIBRARIES + PLATFORM_INFRASTRUCTURE) for path in existing} == {
        _rel(path): [] for path in existing
    }


def test_boundary_scan_covers_the_worker_and_alert_packages() -> None:
    assert "virtual_orders/worker/__init__.py" in {_rel(p) for p in _worker_files()}
    neutral = {_rel(p) for p in _neutral_files()}
    assert {"virtual_orders/alerts/__init__.py", "virtual_orders/analytics/__init__.py"} <= neutral
    assert "virtual_orders/notify/n8n.py" in ADAPTER_FILES
    assert "virtual_orders.worker" in APPLICATION_LAYER
    assert importlib.util.find_spec("apscheduler") is not None
    worker = {_rel(p) for p in _worker_files()}
    assert {
        "virtual_orders/worker/schedule.py", "virtual_orders/worker/jobs.py", "virtual_orders/worker/runner.py",
        WORKER_ENTRYPOINT,
    } <= worker
    assert {
        "virtual_orders/alerts/outbox.py", "virtual_orders/alerts/watch.py", "virtual_orders/alerts/health_watch.py",
        "virtual_orders/analytics/pressure.py", "virtual_orders/evaluator/recheck.py",
        "virtual_orders/readmodels/quality.py",
    } <= neutral
    assert all((SRC / relative).exists() for relative in PLATFORM_PURE_MODULES)
    assert (SRC / "virtual_orders/notify/n8n.py").exists()
    assert "virtual_orders.notify.n8n" in _imported_modules(SRC / COMPOSITION_ROOT)
    assert "virtual_orders.bootstrap" in _imported_modules(SRC / WORKER_ENTRYPOINT)
    assert "virtual_orders/api/routes/watchlist.py" in {_rel(p) for p in _api_files()}


def _root_test_files() -> list[Path]:
    return sorted((ROOT / "tests").rglob("*.py"))


def test_no_module_imports_a_conftest() -> None:
    # Plan 3B close-out entry 20: a conftest imported as a plain module can be registered out of order when test
    # files from different directories share one pytest call. Shared helpers live in tests/integration/support.py.
    offenders = {
        str(path.relative_to(ROOT)): sorted(name for name in _imported_modules(path) if name.endswith(".conftest"))
        for path in _root_test_files()
    }
    assert {name: modules for name, modules in offenders.items() if modules} == {}


DASHBOARD_PROJECT = ROOT / "dashboard"
DASHBOARD_SOURCES = ("dashboard", "tests")  # never dashboard/.venv: that is Streamlit's own site-packages
DASHBOARD_FORBIDDEN = (
    "core", "virtual_orders", "tests", "sqlalchemy", "psycopg", "alembic", "yfinance", "pandas_market_calendars",
    "apscheduler", "fastapi", "starlette", "uvicorn",
)
UI_LIBRARIES = ("dashboard", "streamlit", "plotly")


def _dashboard_files() -> list[Path]:
    files: list[Path] = []
    for folder in DASHBOARD_SOURCES:
        if (DASHBOARD_PROJECT / folder).exists():
            files.extend(sorted((DASHBOARD_PROJECT / folder).rglob("*.py")))
    return files


def _dashboard_rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


@pytest.mark.parametrize("path", _dashboard_files(), ids=_dashboard_rel)
def test_dashboard_talks_to_the_platform_only_over_http(path: Path) -> None:
    assert _offending(path, DASHBOARD_FORBIDDEN) == []


@pytest.mark.parametrize("path", _platform_files() + _core_files(), ids=_rel)
def test_platform_and_core_never_import_the_dashboard_or_ui_libraries(path: Path) -> None:
    assert _offending(path, UI_LIBRARIES) == []


def test_root_tests_never_import_the_dashboard_or_ui_libraries() -> None:
    # D57: the API/dashboard contract crosses projects as recorded JSON fixtures, never as an import.
    offenders = {_dashboard_rel(path): _offending(path, UI_LIBRARIES) for path in _root_test_files()}
    assert {name: modules for name, modules in offenders.items() if modules} == {}


def test_boundary_scan_covers_the_dashboard_project() -> None:
    assert "dashboard/dashboard/__init__.py" in {_dashboard_rel(p) for p in _dashboard_files()}
    assert not any(".venv" in path.parts for path in _dashboard_files())
    assert (DASHBOARD_PROJECT / "pyproject.toml").exists() and (DASHBOARD_PROJECT / "uv.lock").exists()
    root_lock = (ROOT / "uv.lock").read_text()
    for library in ("streamlit", "plotly"):
        assert f'name = "{library}"' not in root_lock, library  # D56: UI libraries never enter the engine lock
        assert importlib.util.find_spec(library) is None, library
    assert "core" in DASHBOARD_FORBIDDEN and "virtual_orders" in DASHBOARD_FORBIDDEN
