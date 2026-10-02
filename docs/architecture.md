# Architecture

<!-- C4-style views (https://c4model.com): extend these diagrams as the service grows. -->

## Context

Who uses Pricewright API and which systems it talks to.

```mermaid
flowchart LR
    user([User or client system]) -->|HTTPS / JSON| service[Pricewright API]
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
