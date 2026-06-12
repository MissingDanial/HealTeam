# HealTeam API — Dockerfile (W1)
#
# Build context for this image is the repository root (`healteam/`).
# See ../../docker-compose.yml for the `context: ..` setting.

# ---- builder ---------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install "poetry==${POETRY_VERSION}"

WORKDIR /build

# Copy API app metadata + the two local monorepo packages that the API depends on.
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml
COPY packages/memory ./packages/memory
COPY packages/agents ./packages/agents
COPY packages/prompts ./packages/prompts

# Resolve and install dependencies from the API's pyproject.toml.
WORKDIR /build/apps/api
RUN poetry install --only main --no-root

# Copy the API source on top (keeps the install cache warm).
COPY apps/api/app ./app

# ---- runtime ---------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Python deps from the builder.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# App source + the local monorepo packages (so path-deps in pyproject.toml
# resolve at runtime even though we never re-install them).
COPY --from=builder /build/apps/api/app /app/app
COPY --from=builder /build/packages /app/../packages
COPY --from=builder /build/packages/prompts /app/packages_prompts

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
