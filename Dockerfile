# One image, many roles (idp, mcp-*, fake-llm, api, evals): the command differs per service.
FROM python:3.13-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY evals ./evals
RUN uv sync --frozen --no-dev \
    && useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /app/evals/reports \
    && chown -R app /app/evals/reports

FROM base AS runtime
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "cfa.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Quality gates run here (ruff, mypy, pytest, pip-audit), never on the host.
FROM base AS dev
RUN uv sync --frozen
COPY tests ./tests
USER app
ENV HOME=/tmp RUFF_CACHE_DIR=/tmp/ruff MYPY_CACHE_DIR=/tmp/mypy
