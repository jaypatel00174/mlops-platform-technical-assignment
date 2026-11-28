## What and why
<!-- Explain why the change is needed, not only what it does. Link the milestone or ADR. -->

## How it was tested
<!-- Commands run and results. New behaviour needs tests; domain logic is test-first. -->

## Review checklist
Full list: `docs/leadership/code-review-checklist.md`

- [ ] Module boundaries respected (`make arch` passes); domain code imports no framework
- [ ] State changes go through the transition tables; multi-step writes are one transaction
- [ ] Concurrency and failure paths considered (constraints, compare-and-swap, idempotency)
- [ ] Errors use the standard envelope; inputs validated; tenant scoping applied
- [ ] No secrets, tokens or personal data; new dependencies are necessary, pinned and scanned
- [ ] Logs are structured with the correlation ID; metrics and health checks updated if behaviour changed
- [ ] Tests are deterministic and never touch live systems
- [ ] Docs, OpenAPI and `.env.example` updated; migrations are backward compatible
- [ ] `make check` is green locally

## Architecture impact
- [ ] None
- [ ] Yes: ADR added or updated (link)
