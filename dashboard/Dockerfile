# Dashboard image (Plan 3C, D56): built from dashboard/uv.lock only; it never contains the engine or its lock.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY dashboard ./dashboard
RUN uv sync --frozen --no-dev --no-editable

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    PATH=/app/.venv/bin:${PATH}

RUN useradd --system --uid 10001 --home-dir /app vo && chown -R vo /app
USER vo
