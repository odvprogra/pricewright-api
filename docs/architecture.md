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

## Rules

- Dependencies point inward; the domain imports no framework or infrastructure library. Enforced by
  the import contracts in `pyproject.toml` (`tests/architecture`).
- Configuration is read only in `main.py` (the composition root) and passed in.
- Every log line is structured JSON with a `request_id` when one exists.
