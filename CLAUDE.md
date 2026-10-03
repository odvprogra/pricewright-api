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
- Work one milestone at a time. Current milestone: **M1 — Tenancy + auth + RBAC**.
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
uv run pricewright-admin create-tenant --help   # operator commands (src/pricewright/cli.py)
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

## Releases

- release-please keeps a release PR open on `main`; merging it tags the version and publishes an
  immutable GitHub release with `openapi.json` attached (`.github/workflows/release.yml`).
- Never bump versions or edit `CHANGELOG.md` by hand: the release PR updates `pyproject.toml`,
  `uv.lock`, `openapi.json` and the changelog together.
- A breaking API change needs a `!` in its Conventional Commit (`feat!:`) so the version reflects
  it.

## Current status

- Done: M0 — generated from python-service-template v1.0.2; ruleset on `main`; brief, README,
  ADR-0002; release pipeline with immutable releases
- In progress: —
- Next: M1 — tenancy, auth and RBAC, including service accounts with scoped API keys
