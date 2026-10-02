# CLAUDE.md — pricewright-api

> Context for AI coding assistants. Keep it short; it points to the standards instead of repeating
> them.

## What this repo is

The API of Pricewright, a multi-tenant B2B quote-to-order platform: transparent pricing engine,
approval workflow and full audit trail. It is the system of record for catalog, prices, quotes and
orders. `pricewright-web`, `erp-mcp-server` and `ops-copilot` consume it only through the
`openapi.json` attached to each release
([ADR-0002](docs/adr/0002-separate-repos-with-a-versioned-openapi-contract.md)). All companies in
its universe (Pricewright, Northfield Supply, Larkspur Tool Co.) are fictional.

## Language

- Talk to the maintainer in Spanish.
- Everything committed to this repo is in English (code, comments, docs, commits, PRs).

## Rules you must follow

- Engineering standards are mandatory:
  [HANDBOOK.md](https://github.com/odvprogra/engineering-standards/blob/v1/HANDBOOK.md)
- Project brief: [docs/brief.md](docs/brief.md). Business rules live in its §4.
- Work one milestone at a time. Current milestone: **M0 — Scaffold**.
- Propose a short plan before coding; ask before deviating from the brief.
- Domain and design decisions follow researched industry practice, with sources in the ADR.
- Write tests with the code. Domain tests use no mocks; use fakes for ports.
- Design patterns only when justified, with an ADR.
- No employer code, data, names or domains — ever.
- No new dependencies without justification. No secrets in the repo.
- One branch and one PR per increment, PR template filled in, CI green, squash merge.

## Commands

```text
just setup | just dev | just check | just test | just lint | just typecheck | just fmt
just migrate | just migration "<message>"
just openapi
```

`just check` must pass before a task is considered done.

## Architecture map

```text
src/pricewright/domain/          pure business logic, no I/O
src/pricewright/application/     use cases + ports (Protocols)
src/pricewright/infrastructure/  adapters (logging, database, ...)
src/pricewright/api/             FastAPI app, routers, Problem Details
src/pricewright/main.py          composition root
```

Read first: `main.py`, `api/app.py`, `api/problems.py`, `settings.py`, `infrastructure/database.py`.

## Decisions already made

See `docs/adr/`. Do not contradict an accepted ADR; propose a new one that supersedes it. The
expected ADRs and their milestones are listed in the brief (§9).

## Current status

- Done: M0 — generated from python-service-template v1.0.2; ruleset on `main`; brief, README and
  ADR-0002
- In progress: M0 — release pipeline (release-please, immutable releases with `openapi.json`)
- Next: M1 — tenancy, auth and RBAC, including service accounts with scoped API keys
