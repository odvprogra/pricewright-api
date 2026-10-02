# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-02

## Context

Pricewright API needs a record of why its significant decisions were made, next to the code, and an
architecture style that keeps business rules testable without infrastructure.

The shared engineering standards already define both, with their alternatives:
[ADR-0001 in engineering-standards](https://github.com/odvprogra/engineering-standards/blob/v1/docs/adr/0001-record-architecture-decisions.md).

## Decision

- We will record decisions specific to this service as ADRs in `docs/adr/`, starting from
  [the template](template.md). Shared decisions are not repeated here; they are linked.
- We will use the **hexagonal-lite** style from the handbook: `domain` ← `application` ←
  `infrastructure` / `api`, wired only in `main.py`. The import contracts in
  `pyproject.toml` are checked by `tests/architecture` on every test run.

## Alternatives considered

See the shared ADR-0001. This service adds no new alternatives.

## Consequences

- **Positive:** decisions are reviewable in pull requests and traceable to the standards.
- **Negative:** a decision made without an ADR is documentation debt; pull requests should link one.
