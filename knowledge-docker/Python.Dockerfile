FROM ghcr.io/astral-sh/uv:0.8.17 AS uv
FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY services/python/pyproject.toml services/python/uv.lock ./
ARG EXTRAS=""
RUN if [ -n "$EXTRAS" ]; then uv sync --frozen --no-dev --no-install-project --extra "$EXTRAS"; else uv sync --frozen --no-dev --no-install-project; fi
COPY services/python/src/ ./src/
RUN if [ -n "$EXTRAS" ]; then uv sync --frozen --no-dev --extra "$EXTRAS"; else uv sync --frozen --no-dev; fi
RUN useradd --uid 10001 --create-home app
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn knowledge_platform.${SERVICE_NAME}.app:app --host 0.0.0.0 --port 8080 --no-access-log"]