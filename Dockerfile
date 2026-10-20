# One image for every run type: demo (offline, no keys), full (live, keys from .env at runtime)
# and snapshot. Keys are never copied in: see .dockerignore.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 DO_NOT_TRACK=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY dbt ./dbt
COPY config ./config
COPY snapshots ./snapshots

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["demo"]
