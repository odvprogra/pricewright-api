# Architecture

<!-- C4-style views (https://c4model.com): extend these diagrams as the service grows. -->

## Context

Who uses Pricewright API and which systems it talks to. Every client depends on a released
`openapi.json`, never on this repository's code or database
([ADR-0002](adr/0002-separate-repos-with-a-versioned-openapi-contract.md)).

```mermaid
flowchart LR
    staff([Sales reps, managers, admins]) --> web[pricewright-web<br/>Next.js]
    web -->|HTTPS / JSON| service[Pricewright API]
    mcp[erp-mcp-server<br/>MCP tools for LLMs] -->|HTTPS / JSON, API key| service
    copilot[ops-copilot<br/>document intake] -->|HTTPS / JSON, API key| service
    service -->|SQL| db[(PostgreSQL)]
```

## Containers

```mermaid
flowchart LR
    subgraph service[Pricewright API]
        api[api<br/>FastAPI, Problem Details, request IDs]
        app[application<br/>use cases, ports]
        domain[domain<br/>business rules]
        infra[infrastructure<br/>adapters]
        api --> app
        app --> domain
        infra -.implements ports.-> app
    end
    infra --> db[(PostgreSQL 18)]
```

## Who is calling, and which tenant

Every request is authenticated, authorized and scoped before any data is read.

```mermaid
sequenceDiagram
    participant C as Client
    participant D as current_principal
    participant U as Use case
    participant W as Unit of work
    participant DB as PostgreSQL
    C->>D: Authorization: Bearer <JWT or pwk_ key>
    D->>D: JWT: verify signature, typ, iss, aud, exp
    D->>W: API key: look up by digest (cross-tenant, auth only)
    D->>U: Principal (tenant, role or scopes)
    U->>U: principal.require(permission) else 403
    U->>W: bind_tenant(principal.tenant_id)
    W->>DB: every query filtered by tenant_id
    DB-->>U: another tenant's row is simply not found (404)
```

## Data

Every tenant-owned table carries `tenant_id`; children point to parents with composite keys, so a
row can never reference another tenant's row (ADR-0006). Prices also carry the currency, tied to the
tenant's by a composite key (ADR-0003). Products and customers are archived, never deleted
(ADR-0016); audit events are append-only (ADR-0013).

```mermaid
erDiagram
    tenants ||--o{ users : "tenant_id"
    tenants ||--o{ service_accounts : "tenant_id"
    service_accounts ||--o{ api_keys : "(tenant_id, service_account_id)"
    users ||--o{ refresh_tokens : "(tenant_id, user_id)"
    tenants ||--o{ product_categories : "tenant_id"
    product_categories |o--o{ products : "(tenant_id, category_id)"
    tenants ||--o{ products : "(tenant_id, currency)"
    tenants ||--o{ customers : "tenant_id"
    tenants ||--o{ audit_events : "tenant_id"
```

## Changing a record

A change and its audit event commit together or not at all (ADR-0013); a stale `If-Match` loses the
compare-and-set (ADR-0012).

```mermaid
sequenceDiagram
    participant C as Client
    participant U as Use case
    participant W as Unit of work
    participant DB as PostgreSQL
    C->>U: PATCH /products/{id}, If-Match: "3"
    U->>W: get product (tenant-scoped)
    U->>U: version 3? apply the change, diff the fields
    U->>W: save (UPDATE ... WHERE version = 3)
    U->>W: add audit event (actor, changes, request ID)
    W->>DB: COMMIT: both rows or neither
    U-->>C: 200, ETag: "4"
```

## Rules

- Dependencies point inward; the domain imports no framework or infrastructure library. Enforced by
  the import contracts in `pyproject.toml` (`tests/architecture`).
- Configuration is read only in `main.py` (the composition root) and passed in.
- Every log line is structured JSON with a `request_id` when one exists.
- Authorization is by permission (`resource:action`), never by role name; service accounts hold
  scopes, never the permissions reserved for people (administration, the audit trail, catalog
  changes, costs) (ADR-0007, ADR-0017).
- Every change appends its audit event in the same unit of work (ADR-0013).
- Lists filter and sort through whitelisted parameters and page with keyset cursors bound to the
  query (ADR-0014).
- Money is a `Decimal` with its currency, never a float, and travels as a decimal string (ADR-0003).
- Use cases bind the unit of work to the caller's tenant; only authentication reads across tenants
  (ADR-0006).
