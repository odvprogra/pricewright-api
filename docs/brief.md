# Pricewright — product brief

> Multi-tenant B2B quote-to-order platform. Two repositories: `pricewright-api` (this one, FastAPI)
> and `pricewright-web` (Next.js, from M7), integrated through the API's versioned OpenAPI contract
> ([ADR-0002](adr/0002-separate-repos-with-a-versioned-openapi-contract.md)).
>
> Pricewright, Northfield Supply and Larkspur Tool Co. are fictional companies.

## 1. Problem

B2B distributors quote prices by hand in spreadsheets: discounts are inconsistent, margins leak,
large discounts get approved over chat with no trace, and sent quotes can't be tied to the final
order. Pricewright centralizes quoting with a transparent pricing engine, an approval workflow and a
full audit trail.

**Northfield Supply**, a mid-size distributor of industrial and office supplies, is the main demo
tenant. **Larkspur Tool Co.** is a second, small tenant that exists to prove tenant isolation.

## 2. Users and roles

| Role            | Can                                                                                          |
| --------------- | -------------------------------------------------------------------------------------------- |
| `sales_rep`     | Manage customers, build quotes, submit for approval, send approved quotes, convert to orders |
| `sales_manager` | Everything a rep can + approve/reject quotes, manage pricing rules                           |
| `admin`         | Everything + manage users, tenant settings, products, price lists                            |

## 3. Domain model (core)

- **Tenant** — a distributor company. Every business row has `tenant_id`. Settings: currency, flat
  `tax_rate`, approval threshold (default 15%).
- **User** — belongs to one tenant, has one role.
- **ServiceAccount** — belongs to one tenant; authenticates with scoped API keys (used by
  `erp-mcp-server` and `ops-copilot`). The authenticated principal covers users and service accounts
  from M1.
- **Customer** — account number, name, tax id, segment/tier (`standard`, `silver`, `gold`), payment
  terms (net days).
- **Product** — SKU, name, category, unit, list price, unit cost, active flag.
- **PricingRule** — typed rule with priority and validity window. Types:
  - `VolumeTier` — discount by quantity bracket for a product or category
  - `CustomerTierDiscount` — discount by customer tier
  - `Promotion` — time-boxed discount
  - `MarginFloorGuard` — not a discount: flags any line whose margin falls below the floor
- **Quote** — aggregate root. Number (per-tenant sequence, e.g. `NF-2026-000123`), customer, lines,
  currency, status, revision (+ link to the revision it supersedes), `valid_until`, totals (list
  subtotal, net subtotal, tax, total), `version` (optimistic locking).
- **QuoteLine** — product, quantity, unit list price, applied adjustments, net unit price, line
  total, margin.
- **PriceBreakdown** — value object explaining _why_ a line costs what it costs (ordered list of
  applied rules with their effect). Shown in the UI.
- **ApprovalRequest** — created when a quote needs approval; decision, decided_by, reason.
- **Order** — created from an accepted quote; snapshot of lines and prices (never recomputed).
- **AuditEvent** — who did what, when, before/after.
- **OutboxEvent** — events to publish reliably (see M5).
- **Supplier**, **SupplierProduct** (supplier SKU → internal product, supplier cost) and
  **PriceChangeProposal** (proposed unit cost / list price changes, approved or rejected by an
  `admin`) — added in M11. Pricewright stays the system of record for catalog and costs:
  `ops-copilot` reads the supplier SKU mapping and only creates proposals; a human approves every
  price change.

## 4. Key business rules

1. Prices are computed by the **pricing engine**, never typed by hand. A manual override is allowed
   only for `sales_manager` and is recorded as its own adjustment with a reason.
2. Pricing pipeline (ordered price waterfall): list price → volume tier → customer tier → promotion
   → manual override → margin floor check. **Stages cascade**: each percentage applies to the
   previous stage's result (10% volume + 5% tier = 14.5%). **Within a stage only one discount
   applies**: the best one for the customer (no stacking two volume tiers). Exclusive promotions (a
   promotion that blocks every other discount) are deferred. This matches industry practice:
   - [Oracle Advanced Pricing](https://docs.oracle.com/cd/E18727_01/doc.121/e13428/T327893T327911.htm):
     buckets cascade; one modifier per incompatibility group.
   - [Salesforce CPQ](https://trailhead.salesforce.com/content/learn/modules/discounting-tools-in-salesforce-cpq/configure-partner-and-distributor-discounts):
     sequential price waterfall.
   - [Dynamics 365](https://learn.microsoft.com/en-us/dynamics365/commerce/discounts-pos): discount
     priorities with exclusive, best price and compounded modes.
   - [SAP SD](https://archive.sap.com/discussions/thread/749296): each condition computed from a
     reference step or subtotal.
3. A quote **requires approval** if its value-weighted discount —
   `1 − (net subtotal / list subtotal)`, before tax and including manual overrides — exceeds the
   tenant threshold (default 15%) **or** any line is below the margin floor.
4. Quote lifecycle (State pattern or an explicit transition table — decided in ADR-0005):

   ```text
   DRAFT ──submit──► PENDING_APPROVAL ──approve──► APPROVED ──send──► SENT ──accept──► ACCEPTED ──convert──► CONVERTED
     │  (no approval needed: submit goes straight to APPROVED)   │reject                  │
     │                                                           ▼                        ├─► EXPIRED (valid_until passed)
     └─cancel──► CANCELLED                                    REJECTED ──revise──► SUPERSEDED  └─revise──► SUPERSEDED

   revise = the current revision becomes SUPERSEDED and a new revision (-R2, -R3 …) starts in DRAFT
   ```

   - Lines are editable only in `DRAFT`.
   - Revising a `SENT`/`REJECTED` quote creates a new **revision** (`NF-2026-000123-R2`) in `DRAFT`;
     the previous revision moves to **`SUPERSEDED`** (terminal, read-only, linked to its successor),
     because the customer may still hold it and it must never be accepted.
   - **Expiration**: every transition checks `valid_until`; acting on an expired quote is a domain
     error (409). A periodic worker job persists `EXPIRED` for listings and reports (from M5; before
     that, only the guard). Correctness never depends on the job's timing.
   - Invalid transitions raise a domain error → 409 Problem Details.

5. Converting to an order is **idempotent** (`Idempotency-Key`) and snapshots prices and tax.
6. Totals: line totals rounded to the currency's minor units (2 decimals for USD) half up, ties away
   from zero (`ROUND_HALF_UP`, [ADR-0003](adr/0003-money-as-an-exact-decimal-with-its-currency.md));
   net subtotal = sum of rounded lines; tax = flat tenant `tax_rate` applied once to the net
   subtotal, rounded the same way; total = net subtotal + tax. Orders snapshot the tax rate and
   amounts. Property-based tests prove totals always reconcile.
7. Tenant isolation: no API call can ever read or write another tenant's data. Tested explicitly.

## 5. Architecture

- Clean architecture per the
  [engineering handbook](https://github.com/odvprogra/engineering-standards/blob/v1/HANDBOOK.md)
  §4–5. Async SQLAlchemy 2.0 + Alembic, PostgreSQL 18. Repository + Unit of Work.
- Tenant scoping enforced in the repository layer from the authenticated principal. Stretch:
  PostgreSQL Row-Level Security as defense in depth (ADR).
- Auth: email + password (argon2), short-lived access JWT + rotating refresh token. Service accounts
  with scoped API keys. Kept minimal; an ADR documents why there is no external IdP.
- **Transactional outbox**: domain events written in the same transaction as the state change; a
  worker relays them using `SELECT ... FOR UPDATE SKIP LOCKED`. Consumers: PDF generation, email
  (Mailpit in dev). No Redis or broker — justified in an ADR. Audit events do not wait for it: they
  are written in the same transaction as the change
  ([ADR-0013](adr/0013-append-only-audit-events-in-the-same-transaction.md)).
- PDF generation of quotes (WeasyPrint or similar) in the worker.
- `pricewright-web` (separate repo, from M7): Next.js; its API client is generated from a pinned
  `openapi.json` release asset of `pricewright-api`, the same way `erp-mcp-server` and `ops-copilot`
  consume the API.
- Contract: `pricewright-api` releases with SemVer (release-please), attaches `openapi.json` to
  every release, and CI flags breaking changes in every pull request (oasdiff against `main`, from
  M2).
- Observability: structlog + OpenTelemetry; `request_id` propagated to the worker through the outbox
  event.

## 6. Milestones

| #   | Milestone                      | Scope                                                                                                                                                                                                                                            | Priority |
| --- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| M0  | Scaffold                       | Generated from `python-service-template` (API + database), CI green with a ruleset on `main`, ADR-0001/0002, brief and README, release-please with an `openapi.json` asset                                                                       | core     |
| M1  | Tenancy + auth + RBAC          | Tenants, users, login/refresh, role guards, service accounts with scoped API keys, tenant isolation tests                                                                                                                                        | core     |
| M2  | Catalog + customers            | CRUD, cursor pagination, filters, validation, audit events                                                                                                                                                                                       | core     |
| M3  | Pricing engine                 | Pure domain, rules pipeline, `PriceBreakdown`, `POST /pricing/preview`; hypothesis tests                                                                                                                                                         | core     |
| M4  | Quotes + lifecycle + approvals | Aggregate, state machine (incl. `SUPERSEDED`, expiration guard), revisions, line-level endpoints (`POST /api/v1/quotes/{id}/lines`, `DELETE /api/v1/quotes/{id}/lines/{line_id}`, both with `If-Match`), tax, approval inbox, optimistic locking | core     |
| M6  | Orders                         | Convert accepted quote, idempotency keys, snapshot                                                                                                                                                                                               | core     |
| —   | **First usable release**       | M0–M4 + M6, seed data (Northfield + Larkspur), complete README, API usable via Swagger                                                                                                                                                           | —        |
| M5  | Outbox + worker                | Outbox table, relay, PDF generation, email on approval/send, expiration job                                                                                                                                                                      | core     |
| M12 | UX & brand                     | Brand guide + design tokens, journeys per role, screen specs, hi-fi clickable mockups of key screens, usability test with 5 participants (SUS), findings in `docs/ux/` (handbook §14)                                                            | core     |
| M7  | Web app (`pricewright-web`)    | Login, quotes list, quote builder with live price breakdown, approval inbox, customer/product screens; built from M12 specs; Storybook; zero axe violations; branded PDF/email templates                                                         | core     |
| M8  | Ops + deploy                   | OTel, deploy (DigitalOcean), public Storybook, demo users, Playwright journeys, GIF in README. Stretch: second usability round on staging                                                                                                        | core     |
| M11 | Supplier price updates         | `Supplier`, `SupplierProduct` mapping, `PriceChangeProposal` + admin approval (needed by `ops-copilot`)                                                                                                                                          | core     |
| M9  | Reorder suggestions            | Endpoint/panel consuming `demand-forecast`                                                                                                                                                                                                       | stretch  |
| M10 | Reporting                      | Win rate, average discount, margin by rep (SQL views)                                                                                                                                                                                            | stretch  |

Rows are in execution order; milestone numbers are stable IDs referenced by other projects.

## 7. Acceptance criteria highlights

- A rep builds a quote with 5 lines; the UI shows a per-line breakdown of which rules applied.
- A 20% discount triggers approval; the manager approves from the inbox; the rep receives an email
  with the PDF.
- Two concurrent edits on the same quote → the second gets 412 Precondition Failed
  ([ADR-0012](adr/0012-optimistic-concurrency-with-etag-and-if-match.md)).
- Retrying "convert to order" with the same `Idempotency-Key` returns the same order, never two.
- A Larkspur user cannot access any Northfield resource (404, not 403, to avoid leaking existence —
  [ADR-0009](adr/0009-not-found-for-other-tenants-resources.md)).
- Usability test: at least 4 of 5 participants complete "build a 5-line quote and submit it" without
  help. The SUS score is reported in the README together with the changes the test caused.
- Critical journeys pass with zero axe violations and can be completed by keyboard only.

## 8. Seed data

A script generates Northfield Supply: ~300 products in ~10 categories, ~80 customers across tiers,
pricing rules, 3 reps + 1 manager + 1 admin, ~150 historical quotes in mixed states. Larkspur Tool
Co.: small dataset. Deterministic (fixed seed).

Product SKUs and categories come from the dataset used by `demand-forecast` (Kaggle "Forecasts for
Product Demand"): the top ~300 products by volume, with deterministic synthetic names of industrial
and office supplies, so forecasts map 1:1 to Pricewright products. A small committed catalog file
holds only codes → names (no demand data), so `just seed` never needs the dataset or a Kaggle token.
Fallback if time is short: a fully synthetic catalog, mapped later.

## 9. Expected ADRs

| ADR  | Topic                                                                                                                                   | Milestone |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| 0001 | [Architecture style, decisions recorded as ADRs](adr/0001-record-architecture-decisions.md)                                             | M0        |
| 0002 | [Separate repos with a versioned OpenAPI contract](adr/0002-separate-repos-with-a-versioned-openapi-contract.md)                        | M0        |
| 0003 | [Money as an exact decimal with its currency, rounded half up](adr/0003-money-as-an-exact-decimal-with-its-currency.md)                 | M2        |
| 0004 | Pricing rule stacking policy and approval metric                                                                                        | M3        |
| 0005 | Quote lifecycle implementation (revisions, expiration)                                                                                  | M4        |
| 0006 | [Shared-schema multi-tenancy, isolated by construction](adr/0006-shared-schema-multi-tenancy.md)                                        | M1        |
| 0007 | [Authentication for users and service accounts](adr/0007-authentication-for-users-and-service-accounts.md)                              | M1        |
| 0008 | Transactional outbox without a broker                                                                                                   | M5        |
| 0009 | [404 for another tenant's resources, 403 for missing permissions](adr/0009-not-found-for-other-tenants-resources.md)                    | M1        |
| 0010 | Supplier price change proposals                                                                                                         | M11       |
| 0011 | [Repository and Unit of Work ports](adr/0011-repository-and-unit-of-work-ports.md) (handbook §5: every pattern gets an ADR)             | M1        |
| 0012 | [Optimistic concurrency with ETag and If-Match](adr/0012-optimistic-concurrency-with-etag-and-if-match.md)                              | M1        |
| 0013 | [Append-only audit events, written in the same transaction](adr/0013-append-only-audit-events-in-the-same-transaction.md)               | M2        |
| 0014 | [List queries: whitelisted filters and sort, keyset cursors](adr/0014-list-queries-with-whitelisted-filters-sort-and-keyset-cursors.md) | M2        |
| 0015 | [Open-ended values in responses](adr/0015-open-ended-values-in-responses.md)                                                            | M2        |
| 0016 | [Catalog and customer master data](adr/0016-catalog-and-customer-master-data.md)                                                        | M2        |
| 0017 | [Costs and margins only for people, behind `costs:read`](adr/0017-costs-only-for-people-with-costs-read.md)                             | M3        |

The business rules in §4 are the agreed inputs for ADR-0003, 0004 and 0005.

## 10. Out of scope

Invoicing, inventory management, taxes beyond a flat rate per tenant, payments, multi-currency
conversion, SSO.
