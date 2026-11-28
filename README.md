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
```

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
| M0 Foundations | Tooling, configuration, Compose database, CI, architecture rules | In review |
| M1 Domain and persistence | State machines, policy, persistence, migrations, seed loader | In progress |
| M2 Registry and governance API | Models, versions, approvals, error envelope, RBAC | Not started |
| M3 Async deployments | Outbox, worker, idempotency, retry, rollback, reconciler | Not started |
| M4 Monitoring and UI | Metrics API, Angular views | Not started |
| M5 Packaging and CI | Full Compose stack, smoke test | Not started |

Screenshots, sample workflows, API documentation location and known limitations are added as the corresponding milestones land.
