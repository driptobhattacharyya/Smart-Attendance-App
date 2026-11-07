FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY scripts ./scripts
RUN sh scripts/download_models.sh /app/models

COPY app ./app
RUN useradd --create-home appuser
USER appuser
ENV PATH="/app/.venv/bin:$PATH" FACE_MODELS_DIR=/app/models PORT=8000
EXPOSE 8000
# Most hosts (Cloud Run, Render, Railway, Fly) pass the port in $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
