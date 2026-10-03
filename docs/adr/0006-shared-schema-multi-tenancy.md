# 0006. Shared-schema multi-tenancy, isolated by construction

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

Every business record (users, customers, products, quotes, orders) belongs to exactly one tenant, a
distributor company. No API call may ever read or write another tenant's data (brief §4, rule 7).
The product targets many small and mid-size distributors, with no regulatory need for physical
separation.

The industry describes three models: a database per tenant (silo), a schema per tenant (bridge) and
shared tables with a tenant identifier (pool). The pool model is the cheapest and simplest to
operate, but "increases the chance for cross-tenant access", so isolation must be designed in, not
relaxed
([AWS SaaS Lens](https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/pool-isolation.html)).
Azure lists one table per tenant as an antipattern and notes that row-level security is complex
enough that many multitenant solutions avoid it
([Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/approaches/storage-data)).
Broken object-level authorization is the top API risk
([OWASP API1:2023](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization)).

## Decision

We will use the **pool model**: one PostgreSQL database, shared tables, and a non-null `tenant_id`
on every tenant-owned table. Isolation is layered:

1. **By construction in the application.** The API opens each unit of work scoped to the
   authenticated principal's tenant. Tenant-owned repositories add that tenant to every query and
   stamp it on every insert, so use cases never handle a tenant id taken from request input.
2. **One narrow exception.** Authentication must find an account before it knows the tenant (login
   by email, API key by hash). Those lookups live in their own explicitly named port, separate from
   every other repository.
3. **In the database.** Tenant-owned tables have `UNIQUE (tenant_id, id)`, and child tables
   reference their parents with composite foreign keys `(tenant_id, parent_id)`, the layout
   [Citus recommends](https://docs.citusdata.com/en/stable/use_cases/multi_tenant.html). A row can
   never point to another tenant's row, whatever the application does.
4. **Tested.** An isolation suite runs every endpoint that takes an id with two tenants (Northfield
   and Larkspur), and a guard test fails when a new endpoint is not covered. The status code is
   ADR-0009's.

PostgreSQL row-level security stays a stretch goal, as a fourth layer.

## Alternatives considered

- **Database per tenant:** the strongest isolation, per-tenant restore and keys, but every migration
  runs once per tenant and connections must be routed. Nothing here requires it.
- **Schema per tenant:** the same operational cost (migrations per schema, `search_path` routing)
  with weaker isolation than separate databases.
- **Row-level security as the main mechanism:** the tenant must be set on every connection, which is
  easy to get wrong with pooling and hard to test. It is worth having only as defense in depth.

## Consequences

- **Positive:** one schema and one migration path; cheap to run; cross-tenant reporting (M10) is
  plain SQL.
- **Negative:** isolation depends on the code paths staying scoped, hence the scoped unit of work,
  the composite keys and the isolation suite. Restoring a single tenant means a selective restore.
  Noisy neighbors are possible; at this scale rate limiting per tenant is enough if it ever matters.
