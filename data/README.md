# Seed data

Sample data from the assignment pack, loaded by `make seed` (`python -m mlops.seed`). The loader is **idempotent**: running it twice leaves the database unchanged.

| File | Content | Becomes |
|---|---|---|
| `seed/registry.json` | 2 models, 4 versions with stage, approval flag and artifact URI | `model`, `model_version` |
| `seed/deployment_events.json` | 3 deployment outcomes | `deployment`, `audit_event` |
| `seed/model_metrics.csv` | 90 daily metric rows, 1 to 30 July 2026, 3 models | `metric_sample` |

## Inconsistencies in the sample data and how the loader handles them

The three files do not fully agree with each other. The loader **creates the missing records instead of dropping data**, and marks them so they are never mistaken for curated registry entries.

| Finding | Handling |
|---|---|
| `valve-health-model` appears in the metrics and in a deployment event but not in `registry.json` | Model created with `owner="unknown"`, `framework="unknown"` and tag `source:seed-inferred` |
| `compressor-anomaly-detector` 2.0.0, `valve-health-model` 1.0.0, 2.0.0 and 3.0.0 appear in metrics or events but not in the registry | Versions created in stage `DRAFT`, approval `PENDING`, synthetic artifact URI, tag `source:seed-inferred` |
| The registry has no artifact digests | A deterministic `sha256:` digest is derived from the artifact URI so digest verification can be exercised |
| Event names are free text | `approval_validation_failed` maps to failure class `POLICY`; `runtime_timeout` maps to `TRANSIENT` |
| Seed deployments pre-date the platform | They are recorded as history only; promotion policy is not re-evaluated for them |

Curated rows carry tag `source:seed`; inferred rows carry `source:seed-inferred`.
