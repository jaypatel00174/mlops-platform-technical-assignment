# MLOps Platform

**Role level: G12 (Principal Software Engineer / Technical Lead)**

A control plane for machine-learning models: register and version models, approve and promote them, deploy asynchronously and safely, monitor health, and roll back. The focus is production-quality engineering, not model training.

> **Status: work in progress.** The architecture is documented in `docs/`. Implementation follows the milestones in `docs/leadership/delivery-plan.md`. See [Project status](#project-status) for what exists today.

## Problem statement

An industrial organization runs many ML models across plants and environments. The platform must govern how a model version gets into production (approval, promotion rules, audit), make deployments safe (asynchronous, idempotent, reversible), and show whether deployed models are healthy.

## Architecture in brief

- **Modular monolith** with four modules (registry, governance, deployment, monitoring), one codebase, one image, two entrypoints (`api`, `worker`).
- **Ports and adapters**: runtime, queue, artifact store, metrics store and identity are interfaces; configuration selects the adapter, so nothing is tied to a cloud or vendor.
- **PostgreSQL** is the system of record and also the work queue (transactional outbox); the database enforces the important rules (one in-flight deployment per model and environment, optimistic locking, idempotency keys, append-only audit).
- **Angular** website talks only to the versioned REST API through a generated typed client.

![Logical architecture](docs/architecture-diagram.png)

Full detail: [architecture](docs/architecture.md), [API design](docs/api-design.md), [ADRs](docs/adr/), [test strategy](docs/test-strategy.md), [answers to the assignment questions](docs/questions.md), [leadership artifacts](docs/leadership/).

## Technology stack

Python 3.12, FastAPI and Pydantic v2, SQLAlchemy 2 and Alembic, PostgreSQL 16, pytest, ruff, mypy (strict), import-linter, uv, Docker and Docker Compose, GitHub Actions. Angular, TypeScript, RxJS and Angular Material arrive with the frontend milestone.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Docker, make.

```bash
make bootstrap     # creates .env from .env.example, installs Python 3.12 dependencies
make up            # starts PostgreSQL (docker compose)
make migrate       # applies database migrations
make seed          # loads the sample data from data/seed (idempotent)
make run           # starts the API on http://localhost:8000 (Swagger UI at /docs)
```

Call the API with a development token (local only; production requires `AUTH_BACKEND=oidc_jwt`):

```bash
TOKEN=$(make token SUBJECT=alice ROLES=admin)
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v1/models?limit=5"
curl http://localhost:8000/health/ready
```

Roles: `viewer`, `ml-engineer`, `approver`, `release-manager`, `admin`, `service`. In Swagger UI, click Authorize and paste the token.

## Tests and quality checks

```bash
make check             # lint, strict types, architecture rules, tests with coverage gate
make test-unit         # fast tests, no database
make test-integration  # database tests (SQLite by default)
TEST_DATABASE_URL=postgresql+psycopg://mlops:mlops@localhost:5432/mlops make test-integration
```

`make arch` enforces the module boundaries from [ADR-001](docs/adr/ADR-001-modular-monolith-with-ports-and-adapters.md): domain code imports no framework, and modules reach each other only through their application layer.

## Project layout

```text
backend/    Python application (src/mlops: kernel, platform, registry, governance, deployment, monitoring)
frontend/   Angular application (later milestone)
data/       seed data and notes on its inconsistencies
docs/       architecture, API design, ADRs, test strategy, leadership artifacts, diagrams
deploy/     Kubernetes and Helm skeleton (design)
ops/        dashboards, alert rules, runbooks (design)
scripts/    cross-platform helper scripts
```

## Branching

Feature branches merge into `dev` by pull request; `dev` is promoted to `main` by a release pull request. See [branching and release flow](docs/leadership/branching-and-release-flow.md).

## Project status

| Milestone | Scope | Status |
|---|---|---|
| Design baseline | Architecture, ADRs, API design, leadership artifacts | Done |
| M0 Foundations | Tooling, configuration, Compose database, CI, architecture rules | Done |
| M1 Domain and persistence | State machines, policy, persistence, migrations, seed loader | Done |
| M2 Registry and governance API | Models, versions, approvals, error envelope, RBAC | Done |
| M3 Async deployments | Outbox, worker, idempotency, retry, rollback, reconciler | Not started |
| M4 Monitoring and UI | Metrics API, Angular views | Not started |
| M5 Packaging and CI | Full Compose stack, smoke test | Not started |

What exists today: the domain rules (lifecycle, promotion policy, approvals with separation of duties, deployment status machine, failure classes, rollback safety, idempotency), the PostgreSQL schema with its concurrency guards, repositories, and an idempotent seed loader. On top of that, the registry and governance HTTP API (`/api/v1/models`, versions, validate, approve, reject, archive, audit events) with RFC 7807 errors, JWT auth, RBAC, ETag/If-Match and cursor pagination. There is **no deployment endpoint, worker or UI yet**; those are M3 and M4.

API notes: approve and reject are actions on the version resource (`POST /api/v1/models/{id}/versions/{v}/approve`). Each request runs in one database transaction, and role checks happen before any data is read.

Known limitations at this stage (the full list will be in `docs/known-limitations.md`):

- `validate` does not yet check that the artifact is reachable.
- Blocking an unapproved version from production is tested at domain level; the API-level scenario arrives with `POST /deployments` in M3.
- Model ids are globally unique. `tenant_id` is stored and every repository read is filtered by it, but composite tenant keys are not in place yet.
- `metric_sample` is a plain table. Monthly partitioning and rollups are a later PostgreSQL-only migration.
- The audit table is made append-only by a database trigger, but the application database role is not yet restricted to insert-only privileges.
- Seeded history is imported as given (stage and approval are not re-derived); see `data/README.md`.

Screenshots, sample workflows and the API documentation location are added as the corresponding milestones land.
