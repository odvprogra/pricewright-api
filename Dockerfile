# syntax=docker/dockerfile:1

# --- Build: resolve the locked dependencies, then install the project (not editable) -------------
FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first: this layer is reused until uv.lock changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- Runtime: only the virtual environment, run as an unprivileged user ---------------------------
FROM python:3.14-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app

# WeasyPrint lays text out with Pango and HarfBuzz. Quote PDFs embed the brand's fonts (ADR-0027);
# DejaVu is the fallback for any glyph they lack.
# fontconfig builds the system font cache here, at build time.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fontconfig fonts-dejavu-core \
    && fc-cache --force \
    && rm -rf /var/lib/apt/lists/*
# The unprivileged user has no home; fontconfig still wants a cache directory it may write.
ENV XDG_CACHE_HOME=/tmp/.cache

WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
# Migrations ship with the image and run as a release step: docker run <image> alembic upgrade head
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app migrations ./migrations

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    LOG_FORMAT=json \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

USER 10001
# The worker runs from the same image: docker run <image> pricewright-api-worker
CMD ["pricewright-api"]
