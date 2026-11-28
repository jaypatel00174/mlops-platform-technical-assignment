# Answers to the Assignment Questions

References point to [architecture.md](architecture.md) and the [ADRs](adr/).

## G12 questions

### 1. How would this scale from 100 to 10,000 models?
Everything on the request path is stateless, so API replicas scale horizontally. Workers scale on queue depth. The outbox sustains the expected deployment rate (about one per minute at 10,000 models) with large headroom; if sustained load grows, a broker adapter replaces the polling consumer while the outbox keeps atomicity. The real volume is metrics (about 29 million rows per day under stated assumptions): monthly partitions, hourly and daily rollups, short raw retention, then a time-series store behind `MetricsStore`. Reads use keyset pagination, read replicas and ETag caching; PgBouncer protects connection limits. See architecture section 11.

```mermaid
flowchart LR
    subgraph s100["About 100 models"]
        a1["1-2 API replicas"]
        w1["1-2 workers"]
        q1["DB outbox"]
        m1["One metrics table"]
    end
    subgraph s10k["About 10,000 models"]
        a2["API replicas scale with HPA"]
        w2["Workers scale on queue depth<br/>KEDA or HPA"]
        q2["Broker adapter<br/>outbox relay keeps atomicity"]
        m2["Monthly partitions, rollups,<br/>later a time-series store"]
        r2["Read replicas, keyset pagination,<br/>ETag caching, PgBouncer"]
    end
    a1 --> a2
    w1 --> w2
    q1 --> q2
    m1 --> m2
    a2 --> r2
```

### 2. How are conflicting promotions prevented?
Two database-level guards: a partial unique index allows only one in-flight deployment per tenant, model and environment, and compare-and-swap on `row_version` protects bindings and versions. The loser gets a deterministic `409` naming the winning deployment. See ADR-003.

```mermaid
sequenceDiagram
    participant A as Release manager A
    participant B as Release manager B
    participant API as API process
    participant DB as PostgreSQL
    A->>API: POST /deployments (pump 2.0.0 to production)
    B->>API: POST /deployments (pump 2.1.0 to production)
    API->>DB: Insert deployment for A
    DB-->>API: OK
    API->>DB: Insert deployment for B
    DB-->>API: Rejected by the partial unique index (one in-flight per model and environment)
    API-->>A: 202 Accepted
    API-->>B: 409 DEPLOYMENT_CONFLICT with the existing deployment id
```

### 3. How is external success versus internal database failure reconciled?
Intent is committed before the side effect (the attempt row). The runtime call is idempotent on `deployment_id`. If the worker dies between the runtime call and the result commit, the reconciler finds attempts stuck in `DEPLOYING`, asks `ModelRuntime.status(deployment_id)`, and converges the database to reality. State change and outbox event share one transaction, so there is no dual write. See ADR-002 and architecture 7.2.

```mermaid
flowchart TD
    crash["Worker dies after the runtime deployed,<br/>before saving the result"] --> stuck["Attempt row still says DEPLOYING<br/>(intent was committed first)"]
    stuck --> scan["Reconciler scan:<br/>attempts in DEPLOYING longer than the timeout"]
    scan --> ask["Ask the runtime:<br/>status(deployment_id)"]
    ask --> live{"Model actually live?"}
    live -->|"yes"| ok["Commit SUCCEEDED, binding, audit<br/>(compare-and-swap)"]
    live -->|"no"| undo["Commit FAILED, undeploy if needed, audit"]
    ok --> done(["Database matches reality"])
    undo --> done
```

### 4. How are multiple model runtimes supported?
`ModelRuntime` is a port with `deploy`, `status`, `undeploy` and `capabilities`. A model version declares a `runtime_type`; configuration maps runtime types to adapters (simulated, KServe, Triton, SageMaker, Vertex). Every adapter must pass the same contract test suite. Validation checks capability compatibility before deploying and before rollback.

```mermaid
flowchart LR
    v["Model version<br/>runtime_type = kserve"] --> cfg["Configuration maps<br/>runtime_type to adapter"]
    cfg --> port["ModelRuntime port<br/>deploy, status, undeploy, capabilities"]
    port --> a1["simulated"]
    port --> a2["kserve"]
    port --> a3["triton"]
    port --> a4["sagemaker"]
    ct["Shared contract tests"] -.->|"every adapter must pass"| port
```

### 5. How would multi-tenancy work?
`tenant_id` is on every table and in every unique key and comes from the validated token. Repositories add the tenant filter, and PostgreSQL row-level security is defense in depth. Per-tenant quotas and rate limits prevent noisy neighbours, and the queue schedules fairly across tenants. Isolation can step up from shared schema to schema-per-tenant to database-per-tenant without changing the API.

```mermaid
flowchart LR
    tok["JWT with tenant claim"] --> ctx["Request context<br/>tenant id"]
    ctx --> repo["Repository layer adds<br/>a tenant filter to every query"]
    repo --> rls[("PostgreSQL<br/>row-level security as a second barrier")]
    ctx --> quota["Per-tenant quotas and rate limits"]
    ctx --> fair["Fair queue scheduling"]
    iso["Isolation can step up:<br/>shared schema, schema per tenant, database per tenant"] -.-> rls
```

### 6. How are large metric volumes partitioned?
Time-partitioned tables (monthly) with an index on `(tenant, model, version, environment, ts)`. Rollup jobs write hourly and daily aggregates; dashboards query rollups for long ranges and raw data only for short ranges. Raw retention is short and configurable. Beyond a threshold, `MetricsStore` is swapped for Timescale, ClickHouse or Prometheus remote-write.

```mermaid
flowchart LR
    ing["POST /metrics batches"] --> raw[("Raw samples<br/>monthly partitions")]
    raw --> job["Aggregator job"]
    job --> roll[("Hourly and daily rollups")]
    raw -->|"drop old partitions"| drop["Short raw retention"]
    q1["Long time range queries"] --> roll
    q2["Short time range queries"] --> raw
    roll -.->|"later"| tsdb[("Time-series store<br/>behind MetricsStore")]
```

### 7. How is unsafe rollback prevented?
Rollback is a validated, audited deployment. In one transaction the platform checks that a previous deployment exists in the same environment, that the target version is approved and not archived or rejected, that its artifact digest still verifies, that the runtime is compatible, and that nothing else is in flight. The binding swap uses compare-and-swap, so a concurrent rollback loses with `409 UNSAFE_ROLLBACK` or `STALE_VERSION`. Production rollback needs role `release-manager`.

```mermaid
flowchart TD
    req["POST /deployments/id/rollback"] --> role{"Caller is release-manager<br/>for production?"}
    role -->|"no"| deny["403 FORBIDDEN"]
    role -->|"yes"| prev{"Binding has a previous deployment<br/>in the same environment?"}
    prev -->|"no"| unsafe["409 UNSAFE_ROLLBACK"]
    prev -->|"yes"| ver{"Target version approved,<br/>not archived or rejected?"}
    ver -->|"no"| unsafe
    ver -->|"yes"| dig{"Artifact digest still verifies?"}
    dig -->|"no"| unsafe
    dig -->|"yes"| cap{"Runtime reports it compatible?"}
    cap -->|"no"| unsafe
    cap -->|"yes"| fly{"Another deployment in flight?"}
    fly -->|"yes"| conflict["409 DEPLOYMENT_CONFLICT"]
    fly -->|"no"| go["Create ROLLBACK deployment<br/>202 Accepted, same async pipeline"]
    go --> cas["On success: swap binding with compare-and-swap<br/>a concurrent rollback loses with 409"]
```

### 8. How are zero-downtime schema migrations handled?
Expand and contract. Release N adds nullable columns or new tables and writes both shapes. Release N+1 reads the new shape. Release N+2 removes the old one. Migrations run as a separate pre-upgrade job, every release supports schema N and N-1, and CI applies the new migration on the previous schema and runs the previous release's tests.

```mermaid
flowchart LR
    r1["Release N<br/>add nullable column or new table<br/>write both shapes"] --> r2["Release N+1<br/>read the new shape"]
    r2 --> r3["Release N+2<br/>drop the old shape"]
    rule["Every release works with schema N and N-1,<br/>so an application rollback never needs a schema rollback"] -.-> r2
```

### 9. How is Angular isolated from backend internals?
Through the versioned OpenAPI contract and a generated typed client. DTOs are mapped to view models in one layer, the UI never imports backend code, and one interceptor normalizes errors into a UI error type with the correlation ID. The API base URL is runtime configuration, so the same image runs everywhere.

```mermaid
flowchart LR
    feat["Feature components<br/>view models only"] --> map["Mappers<br/>DTO to view model"]
    map --> client["Generated typed API client"]
    client --> http["HTTP interceptors<br/>auth, correlation ID, error normalisation"]
    http --> api["REST API /api/v1"]
    spec["OpenAPI spec exported from the backend"] -.->|"generates"| client
    ci["CI OpenAPI diff"] -.->|"blocks breaking changes"| spec
```

### 10. How would work be split across teams?
By module ownership. Registry and governance, deployment and runtime, monitoring and observability, frontend, and platform/SRE each own a module and its contract (lifecycle and policy APIs, `ModelRuntime` and `TaskQueue`, `MetricsStore`, the OpenAPI spec, the Helm chart and pipelines). Import-linter and contract tests keep boundaries honest. See [delivery plan](leadership/delivery-plan.md).

```mermaid
flowchart LR
    reg["Registry and governance squad"] -->|"lifecycle and policy APIs"| ui["Frontend squad"]
    dep["Deployment and runtime squad"] -->|"ModelRuntime, TaskQueue ports"| plat["Platform and SRE"]
    mon["Monitoring and observability squad"] -->|"MetricsStore port, metric API"| ui
    plat -->|"pipelines, environment contract"| reg
    tl["Tech lead: ADRs, boundaries, review standards"] -.-> reg
    tl -.-> dep
    tl -.-> mon
```

## G13 questions (covered for completeness)

### Why was the framework selected?
FastAPI gives typed request and response models, validation and OpenAPI generation from the same code, async support and a strong test client. That makes the contract with the Angular client a by-product of the code.

### How are domain entities represented?
As plain Python domain objects in each module's `domain` layer, independent of the ORM. SQLAlchemy models live in `infrastructure` and are mapped to and from domain objects. Pydantic models describe the API only.

```mermaid
flowchart LR
    dom["Domain objects<br/>plain Python, business rules"] <-->|"mapped in infrastructure"| orm["SQLAlchemy models"]
    dom --> dto["Pydantic schemas<br/>API layer only"]
```

### How are invalid lifecycle transitions prevented?
By explicit transition tables in the domain layer with guards (approver role and separation of duties, staging precondition, rollback safety). Invalid transitions raise typed domain errors mapped to `409 INVALID_TRANSITION`. Stage changes only happen as results of governance actions or deployment outcomes, never by direct field edits. Unit tests cover every allowed and forbidden transition.

```mermaid
flowchart LR
    act["Action requested"] --> tt{"Transition allowed<br/>in the table?"}
    tt -->|"no"| e1["InvalidTransition<br/>409 INVALID_TRANSITION"]
    tt -->|"yes"| g{"Guards pass?<br/>role, separation of duties, staging first"}
    g -->|"no"| e2["Typed domain error<br/>409 or 403"]
    g -->|"yes"| ok["Apply the change and write audit"]
```

### How are API errors standardized?
One RFC 7807 style envelope with `code`, `title`, `detail`, `details` and `correlation_id`, produced by global exception handlers. Domain errors, validation errors and unexpected errors all map to it. See [api-design.md](api-design.md).

```mermaid
flowchart LR
    d["Domain error"] --> h["Global exception handlers"]
    v["Validation error"] --> h
    u["Unexpected error"] --> h
    h --> env["One problem-details envelope<br/>code, title, detail, correlation_id"]
```

### How does Angular handle loading and failure?
Every data view uses the same state components for loading, empty, success and error. HTTP calls go through a generated client; an interceptor normalizes errors and adds the correlation ID, and views show a retry action and the reference. Polling for deployments uses RxJS with backoff and stops at terminal states.

```mermaid
stateDiagram-v2
    [*] --> Loading
    Loading --> Success: data returned
    Loading --> Empty: no items
    Loading --> Error: request failed
    Error --> Loading: user retries
    Success --> Loading: refresh or poll
    Empty --> Loading: refresh
```

### How are tests isolated from live systems?
Fakes and the simulated adapters stand in for the runtime, artifact store and identity provider; the clock and ID generator are injected; integration tests use a disposable PostgreSQL; nothing in tests calls a cloud service. See [test-strategy.md](test-strategy.md).

```mermaid
flowchart LR
    t["Test"] --> app["Application under test"]
    app --> clk["Fixed clock and ID generator"]
    app --> rt["Simulated runtime<br/>failure injection"]
    app --> art["In-memory artifact store"]
    app --> idp["Fake identity provider<br/>test tokens"]
    app --> db[("Disposable PostgreSQL<br/>per test run")]
    live["Cloud services, real runtime, real IdP"] -.-|"never called in tests"| app
```

### How is configuration managed?
Twelve-factor: every setting is an environment variable validated at startup by `pydantic-settings`, failing fast if invalid. `.env.example` documents all variables and CI checks it against the settings schema. Secrets come from the platform secret store. Adapter selection is configuration (`RUNTIME_BACKEND`, `QUEUE_BACKEND`, and so on). The frontend reads a runtime `config.json`.

```mermaid
flowchart LR
    d["Defaults in code"] --> s["Validation at startup<br/>pydantic-settings"]
    e["Environment variables<br/>from values files"] --> s
    sec["Secrets from the secret store"] --> s
    s -->|"invalid or missing"| fail["Fail fast, process exits"]
    s -->|"valid"| run["Select adapters<br/>RUNTIME_BACKEND, QUEUE_BACKEND, ..."]
    ex[".env.example checked against the settings schema in CI"] -.-> s
```

### How would the design extend to a real serving platform?
Implement the `ModelRuntime` port for the target (for example KServe), declare its capabilities, run the shared contract tests against it, and select it by configuration. Deployment workflow, reconciliation, rollback and monitoring are unchanged because they depend only on the port.

```mermaid
flowchart LR
    new["New adapter, for example KServe"] --> cap["Declare capabilities"]
    cap --> contract["Pass the shared contract tests"]
    contract --> cfg["Select with RUNTIME_BACKEND=kserve"]
    cfg --> same["Deployment workflow, reconciler,<br/>rollback and monitoring unchanged"]
```
