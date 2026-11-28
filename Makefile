# Thin wrapper so CI and developers run the same commands.
# Requires: uv (https://docs.astral.sh/uv/), Docker (for the database), make.

UV      ?= uv
BACKEND := backend
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help bootstrap lint fmt typecheck arch test test-unit test-integration check up down migrate seed run worker token clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

bootstrap: ## Create .env from the example and install backend dependencies (Python 3.12)
	@test -f .env || cp .env.example .env
	cd $(BACKEND) && $(UV) sync --frozen

lint: ## Ruff lint and format check
	cd $(BACKEND) && $(UV) run ruff check . && $(UV) run ruff format --check .

fmt: ## Apply ruff fixes and formatting
	cd $(BACKEND) && $(UV) run ruff check --fix . && $(UV) run ruff format .

typecheck: ## mypy in strict mode
	cd $(BACKEND) && $(UV) run mypy

arch: ## Enforce module boundaries and domain purity (import-linter)
	cd $(BACKEND) && $(UV) run lint-imports

test: ## All tests with coverage gate
	cd $(BACKEND) && $(UV) run pytest

test-unit: ## Fast tests that need no database
	cd $(BACKEND) && $(UV) run pytest -m "not integration" --no-cov

test-integration: ## Tests that use a database (set TEST_DATABASE_URL for PostgreSQL)
	cd $(BACKEND) && $(UV) run pytest -m integration --no-cov

check: lint typecheck arch test ## Everything CI runs for the backend

up: ## Start local PostgreSQL
	$(COMPOSE) up -d --wait db

down: ## Stop local services
	$(COMPOSE) down

migrate: ## Apply database migrations
	cd $(BACKEND) && $(UV) run alembic upgrade head

seed: ## Load the sample data from data/seed (idempotent)
	cd $(BACKEND) && $(UV) run python -m mlops.seed --data-dir ../data/seed

run: ## Start the API on http://localhost:8000 (reload on change)
	cd $(BACKEND) && $(UV) run uvicorn mlops.main:app --reload

SUBJECT ?= alice
ROLES ?= admin

worker: ## Start the deployment worker (run alongside `make run`; start several to scale out)
	cd $(BACKEND) && $(UV) run python -m mlops.worker

token: ## Print a development bearer token (make token SUBJECT=bob ROLES=approver)
	@cd $(BACKEND) && $(UV) run python -m mlops.devtoken --subject $(SUBJECT) --roles $(ROLES)

clean: ## Remove caches and build artefacts
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache $(BACKEND)/.coverage $(BACKEND)/htmlcov
