# Engine image for migrate, api and worker (Plan 3C, D47, D56). Built from uv.lock without dev dependencies.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --locked --no-dev

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    PATH=/app/.venv/bin:${PATH} \
    PYTHONPATH=/app/src

RUN useradd --system --uid 10001 --home-dir /app vo && chown -R vo /app
USER vo
