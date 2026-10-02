# CLAUDE.md — pricewright-api

> Context for AI coding assistants. Keep it short; it points to the standards instead of repeating them.

## What this repo is

Multi-tenant B2B quote-to-order API: transparent pricing engine, approval workflow and full audit trail.

## Language

- Talk to the maintainer in Spanish.
- Everything committed to this repo is in English (code, comments, docs, commits, PRs).

## Rules you must follow

- Engineering standards are mandatory:
  [HANDBOOK.md](https://github.com/odvprogra/engineering-standards/blob/v1/HANDBOOK.md)
- Project brief: `docs/brief.md`.
- Work one milestone at a time. Current milestone: **M0 — Scaffold**.
- Propose a short plan before coding; ask before deviating from the brief.
- Write tests with the code. Domain tests use no mocks; use fakes for ports.
- Design patterns only when justified, with an ADR.
- No new dependencies without justification. No secrets in the repo.

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

## Decisions already made

See `docs/adr/`. Do not contradict an accepted ADR; propose a new one that supersedes it.

## Current status

- Done: M0 — generated from python-service-template
- In progress: —
- Known issues / TODO: —
