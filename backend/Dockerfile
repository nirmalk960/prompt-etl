# syntax=docker/dockerfile:1.7

# ---------- Base ----------
FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: gcc for pyarrow wheels, libgomp1 for polars/duckdb.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgomp1 \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install -e ".[dev]"

COPY prompt_etl ./prompt_etl
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY tests ./tests

# ---------- API target ----------
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "prompt_etl.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------- Worker target ----------
FROM base AS worker
CMD ["arq", "prompt_etl.worker.main.WorkerSettings"]
