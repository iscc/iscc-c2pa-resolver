# syntax=docker/dockerfile:1

# Build stage: install the locked runtime dependencies and the package into a virtual environment.
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --no-install-project
COPY iscc_c2pa_resolver ./iscc_c2pa_resolver
RUN uv sync --frozen --no-default-groups --no-editable

# Runtime stage: the virtual environment only, run by an unprivileged user.
FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home resolver
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
USER resolver
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# One worker: the app is I/O bound and async. The fallback fetch route remembers matches per process
# (docs/operations.md), so prefer one process; replicas need sticky sessions for that route.
CMD ["uvicorn", "iscc_c2pa_resolver.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
