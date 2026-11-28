# MLOps Platform UI

Angular 22 (standalone components, signals, zoneless) with Angular Material. It talks only to the
versioned REST API (`/api/v1`) and never imports backend code.

## Run it

```bash
make up migrate seed   # database with sample data
make run               # API on :8000
make worker            # runs the deployments you request
make ui-install        # once
make ui                # UI on http://localhost:4200
```

`make ui` proxies `/api` and `/health` to the API on port 8000 (`proxy.conf.json`), so there is no
CORS to configure. Open http://localhost:4200 and pick a person to sign in. That sign-in exists
only when the API runs with `AUTH_BACKEND=dev_jwt` and `APP_ENV=local` or `test`; with a real
identity provider, use "Use a token instead" and paste a bearer token.

## What is here

| Screen | What it shows |
|---|---|
| Overview | Key numbers, fleet health ring, live deployments per environment, what needs attention, latest deployments (updates live while anything is moving) |
| Models | Search, filter by framework or health, register a model |
| Model | Versions (validate, approve or reject with a reason, deploy, archive), monitoring charts with health limits, version comparison, deployments, audit trail |
| Deployments | Filterable list that follows work in flight, with a detail panel: progress, failure explanation, attempts, timeline, retry and rollback |

Design notes:

- **Errors are first class.** One interceptor turns every failure into an `ApiError` (code, detail,
  reference). Screens show it inline with a retry where retrying can help, and toasts carry a
  "Copy ref" action. A 401 signs you out and brings you back after signing in.
- **Safe to click twice.** Each deploy dialog holds one `Idempotency-Key`, so a double click or a retry
  after a timeout cannot deploy twice.
- **Buttons follow permissions, the server decides.** The UI hides actions your role cannot take
  (`core/auth.service.ts`), but the API still enforces every rule.
- **No chart library.** Charts are plain SVG (`shared/line-chart.ts`): hover, touch or arrow keys
  show exact values, and the health limits are drawn where the platform changes its verdict.
- **Polling is polite.** It runs only while something is moving and the tab is visible.
- Light, dark or system theme; keyboard operable; respects reduced motion.

## The typed client

`src/app/api/schema.d.ts` is generated from `../docs/openapi.json`, which is exported from the
backend. After changing the API run `make openapi` from the repository root; a backend test fails
if the committed contract is out of date.

## Tests

```bash
make ui-test    # unit tests (Vitest): logic, interceptors, error handling, components
make ui-build   # production build with bundle budgets
```
