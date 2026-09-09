FROM ghcr.io/astral-sh/uv:0.8.17 AS uv
FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace/services/python
COPY services/python/pyproject.toml services/python/uv.lock ./
RUN uv sync --frozen --all-extras --no-install-project
COPY services/python/src ./src
COPY services/python/tests ./tests
RUN uv sync --frozen --all-extras
COPY knowledge-docker/scripts /workspace/knowledge-docker/scripts
COPY knowledge-docker/tests /workspace/knowledge-docker/tests
ENV PATH="/workspace/services/python/.venv/bin:$PATH"
WORKDIR /workspace
CMD ["python", "-m", "unittest", "discover", "-s", "knowledge-docker/tests/integration", "-v"]