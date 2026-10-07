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
- Work one milestone at a time. Current milestone: **M5** (transactional outbox, worker, PDF, email,
  expiry job, idempotency-key purge).
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
just seed                                       # reset the local database, load the demo
uv run pricewright-admin create-tenant --help   # operator commands (src/pricewright/cli.py)
uv run pricewright-admin seed --help            # demo data: --as-of, --seed, --check
```

`just check` must pass before a task is considered done.

## Architecture map

```text
src/pricewright/domain/          pure business logic, no I/O
src/pricewright/application/     use cases + ports (Protocols)
src/pricewright/infrastructure/  adapters (logging, database, ...)
src/pricewright/api/             FastAPI app, routers, Problem Details
src/pricewright/demo/            demo tenants and their history, loaded through the use cases
src/pricewright/main.py          composition root
```

Read first: `main.py` (wiring), `application/ports.py` (unit of work and repositories),
`domain/auth.py` (principals and permissions), `api/dependencies.py` (who is calling),
`api/concurrency.py` (ETag / If-Match), `application/audit.py` (the audit trail),
`api/pagination.py` (cursors), `domain/money.py`, `domain/pricing.py` (the engine),
`domain/quote_lifecycle.py` (the transition table), `domain/quotes.py` (the quote aggregate),
`domain/orders.py`, `application/idempotency.py` (`Idempotency-Key`), `tests/fakes.py`,
`demo/seed.py` and `demo/stories.py` (the demo data).

Rules the code relies on:

- Use cases call `principal.require(...)` before any lookup, and bind the unit of work to the
  caller's tenant; only `uow.identities` reads across tenants (ADR-0006, ADR-0009).
- Tenant-owned tables get `UNIQUE (tenant_id, id)` and composite foreign keys.
- Mutable aggregates carry a `version`: ETag out, `If-Match` in (412/428, ADR-0012).
- Every route with an id needs a case in `tests/isolation_cases.py` (a guard test fails otherwise).
- Use cases that change a record append its audit event in the same unit of work
  (`application/audit.py`, ADR-0013).
- Lists take whitelisted filters and `sort`; cursors are bound to the query (`api/pagination.py`,
  ADR-0014). Response values from a growing set are strings with `examples` (ADR-0015).
- Prices come only from the pricing engine (`domain/pricing.py`, ADR-0004); responses leave out
  costs and margins for callers without `costs:read` (ADR-0017).
- Creations (every `POST` that answers 201, except API keys) take an optional `Idempotency-Key`:
  check it first in the unit of work, remember what was created in the same one (ADR-0022).
- Fakes in `tests/fakes.py` enforce the same rules as the adapters; keep them in step.
- Test secrets are generated at runtime: gitleaks flags literals.
- The demo seed (`pricewright.demo`) drives only use cases, as the tenants' people; a new rule the
  demo data must keep goes into `tests/demo_invariants.py` (ADR-0024).

## Decisions already made

See `docs/adr/`. Do not contradict an accepted ADR; propose a new one that supersedes it. The
expected ADRs and their milestones are listed in the brief (§9).

## Releases

- release-please keeps a release PR open on `main`; merging it tags the version and publishes an
  immutable GitHub release with `openapi.json` attached (`.github/workflows/release.yml`).
- Never bump versions or edit `CHANGELOG.md` by hand: the release PR updates `pyproject.toml`,
  `uv.lock`, `openapi.json` and the changelog together.
- A breaking API change needs a `!` in its Conventional Commit (`feat!:`) so the version reflects
  it. `.github/workflows/api-contract.yml` (oasdiff against `main`) fails a pull request whose
  breaking changes are not marked that way.

## Current status

- Done: M0 — scaffold, release pipeline (v0.1.0). M1 — tenants, users, sign-in with rotating refresh
  tokens, role permissions, tenant settings with optimistic concurrency, user management, service
  accounts with API keys, tenant isolation suite (v0.2.0). M2 — errors documented as Problem
  Details, the oasdiff breaking-change check, money (ADR-0003), the audit trail (ADR-0013), list
  queries (ADR-0014), product categories, products and customers (ADR-0016) (v0.3.0). M3 — ISO 4217
  currencies, costs only for people (ADR-0017), pricing rules (ADR-0018), the pricing engine with
  its breakdown and approval metric (ADR-0004), rules managed over the API, `POST /pricing/preview`
  (v0.4.0). M4 — tenant quote settings, the lifecycle as a transition table (ADR-0005), quotes
  priced line by line with a snapshot (ADR-0019), four-eyes approvals (ADR-0020), numbers per tenant
  and year (ADR-0021), quote, line and transition endpoints, the approval inbox (v0.5.0). M6 —
  `Idempotency-Key` on every creation (ADR-0022), the order prefix, orders converted once from
  accepted quotes with their snapshot (ADR-0023), listed and cancelled (v0.6.0)
- Presentable checkpoint — Northfield's catalog from the demand dataset (ADR-0025), demo data
  through the use cases with a history of quotes and orders (ADR-0024), the README's demo and
  walkthrough
- In progress: —
- Next: M5
