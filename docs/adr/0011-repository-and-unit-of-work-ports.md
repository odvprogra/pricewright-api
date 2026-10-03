# 0011. Repository and Unit of Work ports for persistence

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

Use cases live in `application`, which may not import SQLAlchemy or FastAPI (handbook §5; enforced
by the import contracts in `pyproject.toml`). Many operations must save several things atomically: a
tenant and its first admin, a refresh token rotation, a quote and its approval request. And the
handbook asks for use-case tests with in-memory fakes, not a database.

Two classic patterns address exactly this
([Fowler: Repository](https://martinfowler.com/eaaCatalog/repository.html),
[Unit of Work](https://martinfowler.com/eaaCatalog/unitOfWork.html)). In Python, _Architecture
Patterns with Python_ describes the Unit of Work as "our abstraction over the idea of atomic
operations", so the service layer "depends on a thin abstraction"
([Cosmic Python, ch. 6](https://www.cosmicpython.com/book/chapter_06_uow.html)).

## Decision

- `application/ports.py` declares one repository `Protocol` per aggregate, taking and returning
  domain objects, with only the queries the use cases need.
- A `UnitOfWork` `Protocol` exposes the repositories and `commit()`. It is an async context manager:
  leaving it without `commit()`, or with an exception, discards every change. Commits are explicit.
- The SQLAlchemy adapter opens one `AsyncSession` per unit of work. Tables are separate record
  classes (`infrastructure/records.py`); repositories map records to domain objects explicitly, so
  domain classes stay plain dataclasses.
- Use-case tests use in-memory fakes of the ports; each SQLAlchemy repository has integration tests
  on PostgreSQL, which keep the fakes honest.

## Alternatives considered

- **`AsyncSession` in the use cases:** less code, but business flows would need a database to test
  and would import SQLAlchemy, breaking the dependency rule.
- **Classical (imperative) mapping of the domain classes:** no separate records, but ORM
  instrumentation leaks into domain objects (lazy loading, identity, no frozen or slotted
  dataclasses).
- **Active Record (SQLModel):** the domain object is the table row, the coupling hexagonal-lite
  exists to avoid.
- **A generic base repository:** an abstraction "just in case"; per-aggregate repositories with
  explicit queries are easier to read and to keep tenant-scoped (ADR-0006).

## Consequences

- **Positive:** use cases are fast to test with fakes; transaction boundaries are visible in the
  code; persistence can change without touching business logic.
- **Negative:** mapping code per aggregate, and fakes that must behave like the real repositories.
  Adapters type their repository attributes as the ports (mypy treats Protocol attributes as
  invariant).
