# Uniform commands (HANDBOOK §3). Requires uv and Docker; `just setup` once after cloning.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# List the recipes
default:
    @just --list

# Install dependencies and the git hooks
setup:
    uv sync
    uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type commit-msg
    just openapi

# Run locally (starts PostgreSQL in Docker and applies migrations) with auto-reload
dev:
    docker compose up --detach --wait postgres
    uv run alembic upgrade head
    uv run uvicorn pricewright.main:app_factory --factory --reload

# Run the background worker (PostgreSQL and Mailpit in Docker, migrations applied); Ctrl+C stops it
worker:
    docker compose up --detach --wait postgres mailpit
    uv run alembic upgrade head
    uv run pricewright-api-worker

# Check formatting and lint
lint:
    uv run ruff format --check .
    uv run ruff check .

# Type-check (mypy strict)
typecheck:
    uv run mypy

# Run the tests with coverage (integration tests need Docker; skip with: just test -m "not integration")
test *args:
    uv run pytest --cov {{ args }}

# Everything CI checks; must pass before anything lands on main
check: lint typecheck test

# Format and apply safe lint fixes
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Apply database migrations
migrate:
    uv run alembic upgrade head

# Reset the local database and load the demo tenants as of today (ADR-0024): local data is replaced
seed *args:
    uv run pricewright-admin seed --check
    docker compose up --detach --wait postgres
    uv run alembic downgrade base
    uv run alembic upgrade head
    uv run pricewright-admin seed {{ args }}

# Generate a migration from model changes: just migration "add orders table"
migration message:
    uv run alembic revision --autogenerate -m "{{ message }}"

# Pin a pricewright-design release's tokens for the PDF and the emails (D-61), checked against the
# release's attestation: just brand 0.2.0 (then set DESIGN_RELEASE; a test fails until it matches)
brand version:
    gh release download v{{ version }} --repo odvprogra/pricewright-design --pattern tokens.css --pattern tokens.json --dir src/pricewright/infrastructure/templates/brand --clobber
    gh release verify-asset v{{ version }} src/pricewright/infrastructure/templates/brand/tokens.css --repo odvprogra/pricewright-design
    gh release verify-asset v{{ version }} src/pricewright/infrastructure/templates/brand/tokens.json --repo odvprogra/pricewright-design

# Regenerate the committed OpenAPI spec (a test fails when it is stale)
openapi:
    uv run python -c "from pricewright.main import write_openapi; write_openapi()"
