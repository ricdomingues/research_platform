"""Static checks of the production deployment files (D47, D48, D56, D58). No docker binary, no network."""

import re
from pathlib import Path

import yaml

from virtual_orders.config import ENV_VARIABLES, load_settings

ROOT = Path(__file__).resolve().parents[2]
SECRET_NAMES = ("API_KEY", "SECRET", "PASSWORD", "DATABASE_URL", "WEBHOOK")
ERROR_DETAILS = "none"  # D58: probed in Task 1; a boolean Streamlit would make this "false" through a recorded Ruling


def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def services() -> dict:
    return compose()["services"]


def seconds(duration: str) -> int:
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", duration)
    assert match and duration, duration
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def env_example() -> dict[str, str]:
    pairs = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            pairs[name.strip()] = value.strip()
    return pairs


def dockerfile_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def test_the_production_stack_has_exactly_the_spec_services_plus_migrations():
    assert set(services()) == {"postgres", "migrate", "api", "worker", "dashboard"}
    assert "pgdata" in compose()["volumes"]


def test_the_worker_is_one_restarting_instance_with_a_long_grace_period():
    worker = services()["worker"]
    assert worker["command"] == ["python", "-m", "virtual_orders.worker", "run"]
    assert worker["restart"] == "unless-stopped"
    assert seconds(worker["stop_grace_period"]) >= 120
    assert not {"deploy", "scale", "ports"} & set(worker)


def test_migrations_run_once_before_the_api_and_the_worker_with_only_the_database_url():
    stack = services()
    migrate = stack["migrate"]
    assert migrate["command"] == ["alembic", "upgrade", "head"] and migrate["restart"] == "no"
    assert migrate["depends_on"] == {"postgres": {"condition": "service_healthy"}}
    assert set(migrate["environment"]) == {"DATABASE_URL"} and "env_file" not in migrate  # M12: least privilege
    for name in ("api", "worker"):
        assert stack[name]["depends_on"] == {"migrate": {"condition": "service_completed_successfully"}}


def test_the_engine_image_is_built_once_and_the_dashboard_has_its_own_build():
    stack = services()
    assert stack["migrate"]["build"]["context"] == "."
    assert stack["dashboard"]["build"]["context"] == "./dashboard"  # D56: built from dashboard/uv.lock
    for name in ("api", "worker"):
        assert "build" not in stack[name], name  # M11: one build of the engine image
        assert stack[name]["image"] == stack["migrate"]["image"] and stack[name]["pull_policy"] == "never", name
    assert stack["dashboard"]["image"] != stack["migrate"]["image"]


def test_the_api_runs_the_app_factory_and_its_healthcheck_reads_the_key_from_the_environment():
    api = services()["api"]
    assert api["command"][:3] == ["uvicorn", "virtual_orders.bootstrap:app_from_environment", "--factory"]
    check = " ".join(api["healthcheck"]["test"])
    assert "/health" in check and "os.environ['API_KEY']" in check and "/livez" not in check


def test_the_dashboard_gets_only_the_api_url_and_key_and_hides_error_details():
    dashboard = services()["dashboard"]
    assert set(dashboard["environment"]) == {"DASHBOARD_API_URL", "API_KEY"} and "env_file" not in dashboard
    assert dashboard["environment"]["DASHBOARD_API_URL"] == "http://api:8000"
    assert dashboard["depends_on"] == {"api": {"condition": "service_started"}}  # D58
    command = dashboard["command"]
    assert command[:3] == ["streamlit", "run", "dashboard/app.py"]
    assert command[command.index("--client.showErrorDetails") + 1] == ERROR_DETAILS


def test_ports_bind_to_localhost_only_and_postgres_is_not_published():
    for name, service in services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), name
    assert "ports" not in services()["postgres"]


def test_no_secret_value_is_written_in_the_compose_file():
    for name, service in services().items():
        for key, value in (service.get("environment") or {}).items():
            if any(part in key for part in SECRET_NAMES) and key != "DASHBOARD_API_URL":
                assert str(value).startswith("${"), (name, key)
    assert "change-me" not in (ROOT / "docker-compose.yml").read_text()


def test_both_images_are_built_from_their_own_lock_without_dev_dependencies_as_a_non_root_user():
    uv_images = set()
    for dockerfile in (ROOT / "Dockerfile", ROOT / "dashboard" / "Dockerfile"):
        lines = dockerfile_lines(dockerfile)
        assert lines[0].startswith("FROM python:3.12"), dockerfile
        assert "COPY pyproject.toml uv.lock ./" in lines, dockerfile
        assert any(line.startswith("RUN uv sync --locked --no-dev") for line in lines), dockerfile
        users = [line for line in lines if line.startswith("USER ")]
        assert users and users[-1] == "USER vo", dockerfile  # the last USER directive drops root, by name
        copies = [line for line in lines if line.startswith("COPY ")]
        assert not any(".env" in line or "tests" in line for line in copies), dockerfile
        uv_images |= {line.split()[1] for line in copies if line.startswith("COPY --from=ghcr.io/astral-sh/uv:")}
    assert len(uv_images) == 1  # the same pinned uv in both images (D58)
    assert not any("dashboard" in line for line in dockerfile_lines(ROOT / "Dockerfile"))  # D56
    assert {".env", "**/.env", ".git", ".venv", "tests", "dashboard"} <= set((ROOT / ".dockerignore").read_text().split())
    assert {".venv", "tests", ".env", "**/.env"} <= set((ROOT / "dashboard" / ".dockerignore").read_text().split())


def test_env_example_lists_every_setting_with_placeholders_only_and_loads():
    values = env_example()
    assert set(ENV_VARIABLES) - {"GIT_SHA"} <= set(values)
    assert "GIT_SHA" not in values  # the image build arg wins; an env_file value would override code_version
    assert {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"} <= set(values)
    for name in ("API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY", "POSTGRES_PASSWORD"):
        assert values[name] == "change-me", name
    assert "change-me" in values["DATABASE_URL"] and values["N8N_WEBHOOK_URL"] == ""
    load_settings(values)  # a copied .env.example is a valid configuration shape
