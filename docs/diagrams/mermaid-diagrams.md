# Mermaid Diagrams

Diagram-as-code companions to the draw.io file ([`architecture.drawio`](architecture.drawio)). They render natively on GitHub and in most Markdown viewers, and they are easy to review in pull requests.

| # | Diagram | Answers |
|---|---|---|
| 1 | System overview | What are the main parts and how do they connect? |
| 2 | Modules, ports and adapters | How is the code organised and kept vendor neutral? |
| 3 | Deployment sequence | What happens, step by step, when someone deploys a model? |
| 4 | Version lifecycle | Which stage changes are allowed? |
| 5 | Deployment states | Which deployment status changes are allowed? |
| 6 | Failure handling | What happens to a failed deployment? |
| 7 | Data model | Which tables exist and how do they relate? |
| 8 | Delivery pipeline | How does code reach production? |

## 1. System overview

```mermaid
flowchart LR
    users["Users<br/>ML engineer, approver,<br/>release manager, SRE"]
    spa["Angular website<br/>static files on nginx<br/>typed API client"]
    gw["Gateway<br/>TLS, rate limits,<br/>size limits, CORS"]
    idp["OIDC identity provider<br/>Keycloak, Entra ID, Okta"]

    subgraph app["Backend application - one codebase, one image"]
        direction TB
        api["API process<br/>auth, roles, idempotency,<br/>validation, error format"]
        subgraph modules["Modules"]
            direction LR
            reg["Registry"]
            gov["Governance"]
            dep["Deployment"]
            mon["Monitoring"]
        end
        worker["Worker process<br/>deployment executor, reconciler,<br/>metric aggregator, scheduler"]
    end

    db[("PostgreSQL<br/>registry, deployments, audit,<br/>work queue, metrics")]
    runtime["External model runtime<br/>simulated here; KServe, Triton later"]
    artifacts[("Artifact store<br/>filesystem or S3 API")]
    obs["Observability<br/>logs, metrics, traces<br/>open standards"]

    users --> spa
    spa -->|"REST /api/v1 + JWT"| gw
    spa -.->|"OIDC login"| idp
    gw --> api
    api -.->|"verify token"| idp
    api --> modules
    modules --> db
    worker --> db
    worker -->|"deploy, status"| runtime
    modules --> artifacts
    api -.-> obs
    worker -.-> obs
```

## 2. Modules, ports and adapters

The domain depends only on interfaces (ports). Configuration chooses the adapter, so there is no cloud or vendor specific code in business logic.

```mermaid
flowchart TB
    subgraph modules["Domain modules (each: api, application, domain, infrastructure)"]
        reg["Registry"]
        gov["Governance"]
        dep["Deployment"]
        mon["Monitoring"]
    end

    subgraph ports["Ports - interfaces the domain depends on"]
        pRuntime["ModelRuntime"]
        pQueue["TaskQueue"]
        pArtifact["ArtifactStore"]
        pMetrics["MetricsStore"]
        pIdentity["IdentityProvider"]
        pClock["Clock and IdGenerator"]
    end

    subgraph adapters["Adapters - chosen by configuration"]
        aRuntime["simulated runtime<br/>later: KServe, Triton, SageMaker"]
        aQueue["database outbox<br/>later: Redis, RabbitMQ, Kafka"]
        aArtifact["filesystem, S3 API<br/>later: GCS, Azure Blob"]
        aMetrics["SQL tables<br/>later: Timescale, ClickHouse"]
        aIdentity["OIDC and JWT<br/>any compliant provider"]
        aClock["system clock, UUIDs<br/>fakes in tests"]
    end

    dep --> pRuntime
    dep --> pQueue
    reg --> pArtifact
    dep --> pArtifact
    mon --> pMetrics
    gov --> pIdentity
    reg --> pClock
    dep --> pClock

    pRuntime --> aRuntime
    pQueue --> aQueue
    pArtifact --> aArtifact
    pMetrics --> aMetrics
    pIdentity --> aIdentity
    pClock --> aClock

    rules["CI rules (import-linter)<br/>1. domain imports no framework or vendor SDK<br/>2. modules call each other only via application services<br/>3. no module reads another module's tables<br/>4. every adapter passes the shared contract tests"]
    modules -.-> rules
```

## 3. Deployment sequence

```mermaid
sequenceDiagram
    autonumber
    actor Eng as Release manager
    participant UI as Angular UI
    participant API as API process
    participant DB as PostgreSQL
    participant W as Worker
    participant RT as Model runtime
    participant R as Reconciler

    Eng->>UI: Click deploy
    UI->>API: POST /deployments with Idempotency-Key
    API->>API: Check token, role, environment policy
    API->>DB: Look up idempotency key
    Note over API,DB: One transaction writes<br/>deployment (REQUESTED), audit event,<br/>outbox row, idempotency record
    API->>DB: Commit
    API-->>UI: 202 Accepted + Location
    loop Poll with backoff
        UI->>API: GET /deployments/id
        API-->>UI: status
    end

    W->>DB: Claim outbox row (SKIP LOCKED, lease)
    W->>W: Re-validate artifact digest and policy
    W->>DB: Commit attempt row (VALIDATING, DEPLOYING)
    Note over W,DB: Intent is saved BEFORE the external call
    W->>RT: deploy(deployment_id as idempotency key)
    RT-->>W: ready

    alt Runtime ready
        W->>DB: One transaction: SUCCEEDED, binding updated by compare-and-swap, stage advanced, audit written
    else Error or timeout
        W->>DB: FAILED with failure class, audit written
        Note over W,DB: TRANSIENT failures retry with backoff
    end

    opt Worker crashed after the runtime call
        R->>DB: Find attempts stuck in DEPLOYING
        R->>RT: status(deployment_id)
        RT-->>R: actual state
        R->>DB: Converge database to the runtime's state
    end
```

## 4. Version lifecycle

Stage is never edited directly. It changes as a result of a governance action or a deployment outcome.

```mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> VALIDATED: validation checks pass
    VALIDATED --> APPROVED: approver differs from registrant
    APPROVED --> STAGING: staging deployment succeeded
    STAGING --> PRODUCTION: production deployment succeeded
    PRODUCTION --> ARCHIVED: superseded or undeployed
    ARCHIVED --> [*]

    note left of VALIDATED
        A rejected approval keeps the version
        in VALIDATED and records the reason in the audit log
    end note
    note left of PRODUCTION
        Policy: version must be APPROVED
        and have succeeded in STAGING first
    end note
    note right of ARCHIVED
        Any non-live stage may also be
        archived directly
    end note
```

## 5. Deployment states

```mermaid
stateDiagram-v2
    [*] --> REQUESTED
    REQUESTED --> VALIDATING: worker claims outbox row
    VALIDATING --> DEPLOYING: artifact, policy, runtime checks pass
    VALIDATING --> FAILED: classified failure
    DEPLOYING --> SUCCEEDED: runtime ready, binding updated
    DEPLOYING --> FAILED: classified failure
    FAILED --> REQUESTED: retry as a new attempt
    SUCCEEDED --> ROLLED_BACK: replaced by a rollback deployment
    ROLLED_BACK --> [*]

    note right of FAILED
        Only one in-flight deployment per
        model and environment (database rule)
    end note
```

## 6. Failure handling

```mermaid
flowchart TD
    start(["Deployment attempt ends in error"]) --> classify{"Classify the failure"}

    classify -->|"POLICY<br/>not approved, no staging deployment"| manual1["FAILED<br/>no retry until the cause changes"]
    classify -->|"VALIDATION<br/>artifact missing, runtime incompatible"| manual2["FAILED<br/>no retry until fixed"]
    classify -->|"TRANSIENT<br/>timeout, 5xx, network"| budget{"Retry budget left?"}
    classify -->|"PERMANENT_RUNTIME<br/>runtime rejects the model"| manual3["FAILED<br/>manual retry only"]
    classify -->|"INTERNAL<br/>platform bug, database error"| page["FAILED<br/>page the platform team"]

    budget -->|"yes"| backoff["Wait: exponential backoff + jitter<br/>new attempt, visible in timeline"]
    backoff --> again(["Attempt again"])
    budget -->|"no"| manual4["FAILED<br/>manual retry allowed"]

    manual1 --> operator["Operator action<br/>POST /deployments/id/retry"]
    manual2 --> operator
    manual3 --> operator
    manual4 --> operator
    page --> operator
    operator -->|"only from FAILED, re-validates everything"| requested(["REQUESTED, new attempt"])
```

## 7. Data model

```mermaid
erDiagram
    MODEL ||--o{ MODEL_VERSION : has
    MODEL_VERSION ||--o{ APPROVAL : "reviewed by"
    MODEL_VERSION ||--o{ DEPLOYMENT : "deployed as"
    DEPLOYMENT ||--o{ DEPLOYMENT_ATTEMPT : "tried in"
    DEPLOYMENT ||--o{ AUDIT_EVENT : records
    DEPLOYMENT ||--o| OUTBOX_EVENT : triggers
    MODEL ||--o{ ENVIRONMENT_BINDING : "live in"
    DEPLOYMENT ||--o{ ENVIRONMENT_BINDING : "current or previous"
    MODEL_VERSION ||--o{ METRIC_SAMPLE : produces
    METRIC_SAMPLE }o--|| METRIC_ROLLUP : "aggregated into"

    MODEL {
        string id PK
        string tenant_id
        string name
        string owner
        string framework
    }
    MODEL_VERSION {
        string id PK
        string model_id FK
        string version "unique per model"
        string stage
        string approval_status
        string artifact_uri
        string artifact_digest
        int row_version "compare-and-swap"
    }
    DEPLOYMENT {
        string id PK
        string model_version_id FK
        string environment
        string type "DEPLOY or ROLLBACK"
        string status
        string failure_class
        string idempotency_key
        int row_version
    }
    DEPLOYMENT_ATTEMPT {
        string deployment_id FK
        int attempt_no
        string outcome
        string error
    }
    ENVIRONMENT_BINDING {
        string model_id FK
        string environment
        string current_deployment_id FK
        string previous_deployment_id FK
        int row_version
    }
    AUDIT_EVENT {
        string actor
        string action
        string entity
        string correlation_id
    }
    OUTBOX_EVENT {
        string type
        string status
        datetime lease_until
        int attempts
    }
    APPROVAL {
        string model_version_id FK
        string decided_by
        string decision "APPROVED or REJECTED"
        string comment
    }
    METRIC_ROLLUP {
        string model_version_id FK
        string environment
        string resolution "hour or day"
        float latency_p95_ms
    }
    METRIC_SAMPLE {
        datetime ts
        float latency_ms
        float error_rate
        float drift_score
    }
```

Database rules that make this safe: unique `(model, version)`; a partial unique index allowing only one deployment in `REQUESTED`, `VALIDATING` or `DEPLOYING` per model and environment; unique `(tenant, actor, idempotency_key)`; unique binding per `(model, environment)`; audit table insert-only.

## 8. Delivery pipeline

```mermaid
flowchart TB
    dev["Developer<br/>branch and pull request"]
    subgraph ci["CI pipeline"]
        direction LR
        lint["Lint and types<br/>ruff, mypy, eslint"] --> tests["Tests<br/>unit, contract, integration,<br/>import-linter, OpenAPI diff"]
        tests --> sec["Security gates<br/>gitleaks, dependency audit,<br/>Trivy, SBOM"]
        sec --> build["Build multi-arch images<br/>pinned base digests, sign"]
    end
    registry[("Image registry<br/>digest-addressed")]
    gitops["GitOps repo<br/>per-environment Helm values"]
    argo["Argo CD or Flux"]
    staging["Staging cluster"]
    prod["Production cluster"]
    subgraph release["Each release on a cluster"]
        direction LR
        mig["Migration job first<br/>expand and contract"] --> roll["Rolling update<br/>maxUnavailable 0"]
    end

    dev --> ci
    ci --> registry
    registry --> gitops
    gitops --> argo
    argo --> staging
    staging -->|"smoke and acceptance tests pass,<br/>pull request approved"| prod
    prod -.-> release
```
