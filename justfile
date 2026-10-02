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

# Generate a migration from model changes: just migration "add orders table"
migration message:
    uv run alembic revision --autogenerate -m "{{ message }}"

# Regenerate the committed OpenAPI spec (a test fails when it is stale)
openapi:
    uv run python -c "from pricewright.main import write_openapi; write_openapi()"
